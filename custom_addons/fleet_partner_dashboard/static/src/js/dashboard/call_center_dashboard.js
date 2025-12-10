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
      this._isSameArray(this.state.draftSelectedIssueTypes, this.state.selectedIssueTypes)
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
    const labelsFmt = labelsRaw.map(p => p); // simple for now
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
      selectedProducts: [],
      selectedIssueTypes: [],
      draftSelectedProducts: [],
      draftSelectedIssueTypes: [],
      showProducts: false,
      showIssueTypes: false,

      search: '',
      columns: [],

      // modal
      showModal: false,
      modalDriver: null,
      showCharts: false,
    });

    this.filtersBarRef = useRef('filtersBar');
    this.searchBarRef = useRef('searchBar');
    this.modalRef = useRef('cc_modal');

    onMounted(async () => {
      await this._fetchFilters();
      await this._fetchBoard();

      this.state.draftSelectedProducts = [...this.state.selectedProducts];
      this.state.draftSelectedIssueTypes = [...this.state.selectedIssueTypes];

      this._recomputeStickyHeights();
      this._onResizeWin = () => this._recomputeStickyHeights();
      window.addEventListener('resize', this._onResizeWin, { passive: true });
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
      this._destroyCharts();
      this._teardownModalPortal();
    });
  }

  // ---------- RPCs ----------------------------------------------------------
  async _fetchFilters() {
    const { product_types, issue_types } =
      await this.env.services.rpc('/fleet_call_center/filters', {});
    this.state.productTypes = product_types || [];
    this.state.issueTypes = issue_types || [];

    // defaults: all issue types selected
    this.state.selectedIssueTypes = (issue_types || []).map(i => i.key);
  }

  async _fetchBoard() {
    this.state.loading = true;
    const payload = {
      products: this.state.selectedProducts,
      issue_types: this.state.selectedIssueTypes,
    };
    const resp = await this.env.services.rpc('/fleet_call_center/board_data', payload);
    this.state.columns = resp.columns || [];
    this.state.loading = false;
  }

  async applyFilters() {
    this.state.selectedProducts = [...this.state.draftSelectedProducts];
    this.state.selectedIssueTypes = [...this.state.draftSelectedIssueTypes];
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

  // ---------- modal logic ---------------------------------------------------
  async openDriver(card) {
    this.state.modalDriver = {
      driver_id: card.driver_id,
      name: card.name,
      phone: card.phone,
      product: card.product,
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
  }

  toggleModalView() {
    this.state.showCharts = !this.state.showCharts;
    if (!this.state.showCharts) {
      this._destroyCharts();
      return;
    }

    // draw charts on next frame when DOM is ready
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const host = this.modalRef?.el;
        if (!host || !this.state.modalDriver?.series) return;
        const s = this.state.modalDriver.series;
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

  closeModal() {
    this.state.showModal = false;
    this.state.modalDriver = null;
    this.state.showCharts = false;
    this._destroyCharts();
    this._teardownModalPortal();
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
