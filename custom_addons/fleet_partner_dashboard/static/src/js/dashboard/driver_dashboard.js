/** @odoo-module **/

console.log("✅ OwlDriverDashboard JS is loaded!");

import { registry } from "@web/core/registry"
const { Component } = owl
import { qweb } from "web.core";

export class OwlDriverDashboard extends Component {}

OwlDriverDashboard.template = "owl.OwlDriverDashboard"

registry.category("actions").add("owl.driver_dashboard", OwlDriverDashboard)