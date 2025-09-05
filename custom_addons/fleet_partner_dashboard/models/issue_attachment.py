# models/issue_attachment.py
from odoo import models, fields, api
from typing import Optional, cast
import requests

class IssueAttachment(models.Model):
    _name = "x_issue_attachment"
    _description = "External (Supabase) attachment for an Issue"
    _order = "id desc"

    issue_id = fields.Many2one("x_fleet_issue", required=True, ondelete="cascade")

    # Field descriptors cast to runtime types for Pylance
    ext_attachment_id = cast(str, fields.Char(string="External Attachment ID", index=True))
    bucket            = cast(str, fields.Char(required=True))        # e.g. "anda-media"
    key               = cast(str, fields.Char(required=True))        # e.g. "attachments/video/clip.mp4"

    # Keep datetimes as Optional[str] (Odoo uses strings)
    created_at            = cast(Optional[str], fields.Datetime(string="Created At"))
    signed_url            = cast(Optional[str], fields.Char(readonly=True))
    signed_url_expires_at = cast(Optional[str], fields.Datetime(readonly=True))

    kind = fields.Selection(
        [("image","Image"), ("video","Video"), ("audio","Audio"), ("pdf","PDF"), ("other","Other")],
        compute="_compute_kind", store=True
    )

    _sql_constraints = [
        ("unique_ext_attachment", "unique(ext_attachment_id)", "Attachment already synced."),
    ]

    @api.depends("key")
    def _compute_kind(self):
        for rec in self:
            k = (rec.key or "").lower()
            if k.endswith((".png",".jpg",".jpeg",".gif",".webp",".avif")) or "/image/" in k:
                rec.kind = "image"
            elif k.endswith((".mp4",".webm",".mov",".m4v")) or "/video/" in k:
                rec.kind = "video"
            elif k.endswith((".mp3",".wav",".ogg",".m4a")) or "/audio/" in k:
                rec.kind = "audio"
            elif k.endswith(".pdf"):
                rec.kind = "pdf"
            else:
                rec.kind = "other"

    def ensure_fresh_url(self, expires: int = 600):
        """Always mint a new signed URL (simplest & Pylance-friendly)."""
        for rec in self:
            signer     = rec.env["ir.config_parameter"].sudo().get_param("media_signer.base_url")
            signer_key = rec.env["ir.config_parameter"].sudo().get_param("media_signer.api_key")

            if not (signer and signer_key and rec.bucket and rec.key):
                rec.signed_url = None
                rec.signed_url_expires_at = None
                continue

            try:
                r = requests.post(
                    f"{signer}/sign",
                    json={"bucket": rec.bucket, "object": rec.key, "expiresIn": expires},
                    headers={"x-api-key": signer_key},
                    timeout=8,
                )
                if not r.ok:
                    rec.signed_url = None
                    rec.signed_url_expires_at = None
                    continue

                data = r.json()
                rec.signed_url = data.get("url") or None
                # Odoo helper returns a string datetime; cast to keep Pylance happy
                rec.signed_url_expires_at = cast(
                    str,
                    fields.Datetime.add(fields.Datetime.now(), seconds=expires - 30)
                )
            except Exception:
                rec.signed_url = None
                rec.signed_url_expires_at = None
