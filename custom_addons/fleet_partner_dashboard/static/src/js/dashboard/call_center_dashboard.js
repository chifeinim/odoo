/** @odoo-module **/

import { registry } from '@web/core/registry';
const { Component, hooks } = owl;
const { onMounted, onPatched, onWillUnmount, useRef, useState } = hooks;

export class OwlCallCenterDashboard extends Component {

  // ---------- small helpers -------------------------------------------------
  formatNumber(v, d = 0) {
    const n = Number(v);
    if (Number.isNaN(n)) return v ?? '';
    return new Intl.NumberFormat('en-GB', {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    }).format(n);
  }

  formatDateYMDToDMY(ymd) {
    if (!ymd) return '';
    const [y, m, d] = String(ymd).split('-').map(Number);
    if (!y || !m || !d) return ymd;
    return `${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}/${y}`;
  }

  digits(s) {
    return (s || '').replace(/\D/g, '');
  }

  _isSameArray(a, b) {
    if (a === b) return true;
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    if (a.length !== b.length) return false;
    const A = [...a].sort();
    const B = [...b].sort();
    return A.every((v, i) => String(v) === String(B[i]));
  }

  isDirty() {
    return !(
      this._isSameArray(this.state.draftSelectedProducts, this.state.selectedProducts) &&
      this._isSameArray(this.state.draftSelectedIssueTypes, this.state.selectedIssueTypes) &&
      this._isSameArray(this.state.draftSelectedDriverTypes, this.state.selectedDriverTypes) // NEW
    );
  }


  // ---------- sticky filters/search heights ---------------------------------
  _recomputeStickyHeights() {
    const f = this.filtersBarRef?.el;
    const s = this.searchBarRef?.el;
    const fh = f ? f.getBoundingClientRect().height : 0;
    const sh = s ? s.getBoundingClientRect().height : 0;
    if (this.el) {
      this.el.style.setProperty('--sticky-filters-h', `${Math.ceil(fh)}px`);
      this.el.style.setProperty('--sticky-search-h', `${Math.ceil(sh)}px`);
    }
  }

  // ---------- charts --------------------------------------------------------
  constructor(...args) {
    super(...args);
    this._charts = {};
    this._modalPortaled = false;
    this._modalPlaceholder = null;
  }

  renderBarChart(canvas, series, title) {
    if (!canvas || !series) return;
    const id = canvas.id || Math.random().toString(36).slice(2);
    canvas.id = id;

    if (this._charts[id]) {
      try { this._charts[id].destroy(); } catch (e) {}
    }

    const labelsRaw = series.map(p => p.period);
    const labelsFmt = labelsRaw.map(p => p);
    const values = series.map(p => p.value);

    // global Chart from chart.umd.min.js
    this._charts[id] = new window.Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: labelsFmt,
        datasets: [{ label: title, data: values, borderWidth: 0 }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          title: { display: false },
          tooltip: {
            enabled: true,
            callbacks: {
              title: items => (items?.length ? items[0].label : ''),
              label: ctx => `${title}: ${ctx.parsed.y}`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false, drawBorder: false },
          },
          y: {
            grid: { display: false, drawBorder: false },
            ticks: { display: true },
          },
        },
        layout: { padding: 0 },
      },
    });
  }

  _destroyCharts() {
    Object.values(this._charts || {}).forEach(ch => {
      try { ch.destroy(); } catch (e) {}
    });
    this._charts = {};
  }

  // ---------- OWL setup -----------------------------------------------------
  setup() {
    this.state = useState({
      loading: true,

      // filters
      productTypes: [],
      issueTypes: [],
      statusOptions: [],
      driverTypes: [],

      selectedProducts: [],
      selectedIssueTypes: [],
      selectedDriverTypes: [],
      draftSelectedProducts: [],
      draftSelectedIssueTypes: [],
      draftSelectedDriverTypes: [],
      showProducts: false,
      showIssueTypes: false,
      showDriverTypes: false,

      search: '',
      columns: [],

      // driver detail modal
      showModal: false,
      modalDriver: null,
      showCharts: false,

      // issue detail modal
      issueModalOpen: false,
      issueDetail: null,

      // status change modal
      statusModalOpen: false,
      statusModal: {
        issue_id: null,
        current_status: '',
        current_status_label: '',
        new_status: '',
        note: '',
      },
    });

    this.filtersBarRef = useRef('filtersBar');
    this.searchBarRef = useRef('searchBar');
    this.modalRef = useRef('cc_modal');

    onMounted(async () => {
      await this._fetchFilters();
      await this._fetchBoard();

      this.state.draftSelectedProducts = [...this.state.selectedProducts];
      this.state.draftSelectedIssueTypes = [...this.state.selectedIssueTypes];
      this.state.draftSelectedDriverTypes = [...this.state.selectedDriverTypes]; // NEW


      this._recomputeStickyHeights();
      this._onResizeWin = () => this._recomputeStickyHeights();
      window.addEventListener('resize', this._onResizeWin, { passive: true });

      // ESC key closes nested modals (status > issue > driver)
      this._onKeyUp = (ev) => {
        if (ev.key === 'Escape' || ev.key === 'Esc') {
          if (this.state.statusModalOpen) this.closeStatusModal();
          else if (this.state.issueModalOpen) this.closeIssueModal();
          else if (this.state.showModal) this.closeModal();
        }
      };
      window.addEventListener('keyup', this._onKeyUp);
    });

    onPatched(() => {
      this._recomputeStickyHeights();

      // modal portal
      this._updateModalPortal();

      // charts
      if (this.state.showModal && this.state.showCharts && this.state.modalDriver?.series) {
        const host = (this.modalRef && this.modalRef.el) || document;
        const s = this.state.modalDriver.series;
        this.renderBarChart(host.querySelector('#cc_chart_cash'), s.cashEarned, 'Gross Revenue');
        this.renderBarChart(host.querySelector('#cc_chart_trips'), s.trips, 'Trips');
        this.renderBarChart(host.querySelector('#cc_chart_hours'), s.supplyHours, 'Hours Online');
        this.renderBarChart(host.querySelector('#cc_chart_trph'), s.tripsPerHour, 'Trips per Hour');
        this.renderBarChart(host.querySelector('#cc_chart_acc'), s.acceptanceRate, 'Acceptance Rate %');
        this.renderBarChart(host.querySelector('#cc_chart_comp'), s.completionRate, 'Completion Rate %');
        this.renderBarChart(host.querySelector('#cc_chart_cbd'), s.cancelledByDriverPct, 'Cancelled by Driver %');
      }
    });

    onWillUnmount(() => {
      if (this._onResizeWin) {
        window.removeEventListener('resize', this._onResizeWin);
      }
      if (this._onKeyUp) {
        window.removeEventListener('keyup', this._onKeyUp);
      }
      this._destroyCharts();
      this._teardownModalPortal();
    });
  }

  // ---------- RPCs ----------------------------------------------------------
  async _fetchFilters() {
    const { product_types, issue_types, driver_types } =
      await this.env.services.rpc('/fleet_call_center/filters', {});

    this.state.productTypes = product_types || [];
    this.state.issueTypes = issue_types || [];
    this.state.driverTypes = driver_types || [];   // NEW

    // default: all issue types selected
    this.state.selectedIssueTypes = (issue_types || []).map(i => i.key);

    // NEW: default all driver types selected
    this.state.selectedDriverTypes = (driver_types || []).map(d => d.key);

    // hard-code status options to match ISSUE_STATUS_SELECTION
    this.state.statusOptions = [
      { key: 'unresolved', 'label': 'Not Started' },
      { key: 'resolved', 'label': 'Resolved' },
      { key: 'requires_follow_up_call', 'label': 'Requires Follow-Up Call' },
      { key: 'invited_to_office', 'label': 'Invited To Office' },
      { key: 'invited_to_workshop', 'label': 'Invited To Workshop' },
    ];
  }


  async _fetchBoard() {
    this.state.loading = true;
    const payload = {
      products: this.state.selectedProducts,
      issue_types: this.state.selectedIssueTypes,
      driver_types: this.state.selectedDriverTypes, // NEW
    };
    const resp = await this.env.services.rpc('/fleet_call_center/board_data', payload);
    this.state.columns = resp.columns || [];
    this.state.loading = false;
  }


  async applyFilters() {
    this.state.selectedProducts = [...this.state.draftSelectedProducts];
    this.state.selectedIssueTypes = [...this.state.draftSelectedIssueTypes];
    this.state.selectedDriverTypes = [...this.state.draftSelectedDriverTypes]; // NEW
    await this._fetchBoard();
  }


  // ---------- filters / search ---------------------------------------------
  toggleDropdown(flagName) {
    this.state[flagName] = !this.state[flagName];
  }

  toggleFilter(listName, value) {
    const key = `draft${listName[0].toUpperCase()}${listName.slice(1)}`;
    if (!Array.isArray(this.state[key])) this.state[key] = [];
    const list = this.state[key];
    const i = list.indexOf(value);
    if (i === -1) list.push(value); else list.splice(i, 1);
  }

  onSearchChange(ev) {
    this.state.search = ev.target.value || '';
  }

  // ---------- board helpers -------------------------------------------------
  visibleCards(col) {
    const q = (this.state.search || '').trim().toLowerCase();
    const qd = this.digits(q);
    const cards = col.cards || [];
    if (!q) return cards;

    return cards.filter(c => {
      const nameHit = (c.name || '').toLowerCase().includes(q);
      const phoneHit = qd ? this.digits(c.phone || '').includes(qd) : false;
      return nameHit || phoneHit;
    });
  }

  responsiveCards(col) {
    return this.visibleCards(col).filter(c => !c.is_unresponsive);
  }

  unresponsiveCards(col) {
    return this.visibleCards(col).filter(c => c.is_unresponsive);
  }

  // ---------- modal logic: driver detail -----------------------------------
  async openDriver(card) {
    this.state.modalDriver = {
      driver_id: card.driver_id,
      name: card.name,
      phone: card.phone,
      product: card.product,
      driver_type: '',
      driver_type_label: '',
      hire_date: '',
      is_unresponsive_today: false,
      cards: null,
      series: null,
      issues: [],
      metrics_range: null,
      issues_range: null,
    };

    this.state.showModal = true;
    this.state.showCharts = false;

    try { document.body.classList.add('lp-modal-open'); } catch (e) {}

    const res = await this.env.services.rpc('/fleet_call_center/driver_detail', {
      driver_id: card.driver_id,
    });

    this.state.modalDriver.cards = res.cards || {};
    this.state.modalDriver.series = res.series || {};
    this.state.modalDriver.issues = res.issues || [];
    this.state.modalDriver.metrics_range = res.metrics_range || null;
    this.state.modalDriver.issues_range = res.issues_range || null;

    if (res.driver) {
      const d = res.driver;
      this.state.modalDriver.name = d.name || this.state.modalDriver.name;
      this.state.modalDriver.phone = d.phone || this.state.modalDriver.phone;
      this.state.modalDriver.product = d.product || this.state.modalDriver.product;
      this.state.modalDriver.driver_type = d.type || '';
      this.state.modalDriver.driver_type_label = d.type_label || '';
      this.state.modalDriver.hire_date = d.hire_date || '';
      this.state.modalDriver.is_unresponsive_today = !!d.is_unresponsive_today;
    }
  }

  toggleModalView() {
    this.state.showCharts = !this.state.showCharts;
    if (!this.state.showCharts) {
      this._destroyCharts();
      return;
    }

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const host = this.modalRef?.el;
        const s = this.state.modalDriver?.series;
        if (!host || !s) return;
        this.renderBarChart(host.querySelector('#cc_chart_cash'), s.cashEarned, 'Gross Revenue');
        this.renderBarChart(host.querySelector('#cc_chart_trips'), s.trips, 'Trips');
        this.renderBarChart(host.querySelector('#cc_chart_hours'), s.supplyHours, 'Hours Online');
        this.renderBarChart(host.querySelector('#cc_chart_trph'), s.tripsPerHour, 'Trips per Hour');
        this.renderBarChart(host.querySelector('#cc_chart_acc'), s.acceptanceRate, 'Acceptance Rate %');
        this.renderBarChart(host.querySelector('#cc_chart_comp'), s.completionRate, 'Completion Rate %');
        this.renderBarChart(host.querySelector('#cc_chart_cbd'), s.cancelledByDriverPct, 'Cancelled by Driver %');
      });
    });
  }

  async toggleDriverUnresponsive() {
    const drv = this.state.modalDriver;
    if (!drv || !drv.driver_id) {
      return;
    }

    const currently = !!drv.is_unresponsive_today;

    const res = await this.env.services.rpc(
      '/fleet_call_center/set_driver_unresponsive',
      {
        driver_id: drv.driver_id,
        unresponsive: !currently,
      }
    );

    if (!res || !res.ok) {
      window.alert(res && res.error ? res.error : 'Failed to update driver.');
      return;
    }

    drv.is_unresponsive_today = !!res.is_unresponsive_today;

    // Refresh board so the card jumps between "responsive" and "unresponsive" section
    await this._fetchBoard();
  }


  closeModal() {
    this.state.showModal = false;
    this.state.modalDriver = null;
    this.state.showCharts = false;
    this.state.issueModalOpen = false;
    this.state.statusModalOpen = false;
    this.state.issueDetail = null;
    this._destroyCharts();
    this._teardownModalPortal();
  }

  // ---------- modal logic: issue detail ------------------------------------
  async openIssue(issueRow) {
    this.state.issueModalOpen = true;
    this.state.issueDetail = null;

    const res = await this.env.services.rpc('/fleet_call_center/issue_detail', {
      issue_id: issueRow.id,
    });

    if (res && res.ok) {
      this.state.issueDetail = res;
    } else {
      this.state.issueModalOpen = false;
    }
  }

  closeIssueModal() {
    this.state.issueModalOpen = false;
    this.state.issueDetail = null;
  }

  // ---------- modal logic: status change -----------------------------------
  openStatusModal(issueRow) {
    const currentLabel = issueRow.status || issueRow.status_raw || '';
    this.state.statusModal = {
      issue_id: issueRow.id,
      current_status: issueRow.status_raw,
      current_status_label: currentLabel,
      new_status: issueRow.status_raw,
      note: '',
    };
    this.state.statusModalOpen = true;
  }

  closeStatusModal() {
    this.state.statusModalOpen = false;
  }

  onStatusChange(ev) {
    this.state.statusModal.new_status = ev.target.value;
  }

  onStatusNote(ev) {
    this.state.statusModal.note = ev.target.value || '';
  }

  async submitStatusChange() {
    const payload = {
      issue_id: this.state.statusModal.issue_id,
      new_status: this.state.statusModal.new_status,
      note: this.state.statusModal.note,
    };

    const res = await this.env.services.rpc('/fleet_call_center/update_issue_status', payload);

    if (!res || !res.ok) {
      window.alert(res && res.error ? res.error : 'Failed to update status');
      return;
    }

    const updated = res.issue;

    // Update issue row in driver modal
    if (this.state.modalDriver && Array.isArray(this.state.modalDriver.issues)) {
      const arr = this.state.modalDriver.issues;
      const idx = arr.findIndex(i => i.id === updated.id);
      if (idx !== -1) {
        arr[idx] = {
          ...arr[idx],
          status: updated.status_label || updated.status || arr[idx].status,
          status_raw: updated.status || arr[idx].status_raw,
          date_resolved: updated.date_resolved || arr[idx].date_resolved,
        };
      }
    }

    // If issue detail modal is open for this issue, refresh it
    if (this.state.issueModalOpen &&
        this.state.issueDetail &&
        this.state.issueDetail.issue &&
        this.state.issueDetail.issue.id === updated.id) {
      await this.openIssue({ id: updated.id });
    }

    // Refresh board so cards move columns if needed
    await this._fetchBoard();

    this.closeStatusModal();
  }

  // ---------- modal portal to <body> ---------------------------------------
  _updateModalPortal() {
    const el = this.modalRef?.el;
    if (this.state.showModal && el && !this._modalPortaled) {
      if (!this._modalPlaceholder && el.parentNode) {
        this._modalPlaceholder = document.createComment('cc-modal-anchor');
        el.parentNode.insertBefore(this._modalPlaceholder, el);
      }
      document.body.appendChild(el);
      this._modalPortaled = true;
      try { document.body.classList.add('lp-modal-open'); } catch (e) {}
    }

    if (!this.state.showModal && this._modalPortaled && this._modalPlaceholder?.parentNode && el) {
      this._modalPlaceholder.parentNode.insertBefore(el, this._modalPlaceholder);
      this._modalPortaled = false;
      try { document.body.classList.remove('lp-modal-open'); } catch (e) {}
    }
  }

  _teardownModalPortal() {
    const el = this.modalRef?.el;
    if (this._modalPortaled && this._modalPlaceholder?.parentNode && el) {
      this._modalPlaceholder.parentNode.insertBefore(el, this._modalPlaceholder);
    }
    this._modalPortaled = false;
    try { document.body.classList.remove('lp-modal-open'); } catch (e) {}
  }
}

OwlCallCenterDashboard.template = 'owl.OwlCallCenterDashboard';
registry.category('actions').add('owl.call_center_dashboard', OwlCallCenterDashboard);
