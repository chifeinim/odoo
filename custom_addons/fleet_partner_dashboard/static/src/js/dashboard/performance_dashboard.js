/** @odoo-module **/

const { Component, hooks } = owl;
const { onMounted, onPatched, useState, useRef } = hooks;
import { registry } from '@web/core/registry';

export class OwlPerformanceDashboard extends Component {
  formatNumber(value, digits = 0) {
    const n = Number(value);
    if (Number.isNaN(n)) return value ?? '';
    return new Intl.NumberFormat('en-GB', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n);
  }

  qualityClass(score) {
    const s = (score || '').toString().trim().toLowerCase();
    if (s === 'low performer') return 'text-danger font-weight-bold fw-bold';
    if (s === 'average performer') return 'text-warning font-weight-bold fw-bold';
    if (s === 'high performer') return 'text-success font-weight-bold fw-bold';
    return '';
  }
  setSort(key) {
    if (this.state.sortKey === key) {
      this.state.sortDir = this.state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.state.sortKey = key;
      this.state.sortDir = 'asc';
    }
  }

  // robust comparator that handles numbers, strings, booleans, and ISO-ish dates
  _compareByKey(a, b, key) {
    const ax = a?.[key];
    const bx = b?.[key];

    const norm = (v) => {
      if (v === undefined || v === null) return null;
      if (typeof v === 'boolean') return v ? 1 : 0;
      if (typeof v === 'number') return v;
      if (typeof v === 'string') return v.trim();
      return v;
    };

    let av = norm(ax);
    let bv = norm(bx);

    // nulls last
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;

    // try date compare when key looks like a date field or value matches YYYY-MM-DD
    const looksLikeDate = key === 'hire_date' || (/^\d{4}-\d{2}-\d{2}/.test(String(av)) && /^\d{4}-\d{2}-\d{2}/.test(String(bv)));
    if (looksLikeDate) {
      const ta = Date.parse(av);
      const tb = Date.parse(bv);
      if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb;
    }

    // numeric compare
    if (typeof av === 'number' && typeof bv === 'number') {
      return av - bv;
    }

    // boolean already coerced to number above
    if (typeof av === 'number' && typeof bv !== 'number') return -1;
    if (typeof av !== 'number' && typeof bv === 'number') return 1;

    // string-ish compare (case/locale aware & numeric segments)
    return String(av).toLocaleLowerCase().localeCompare(String(bv).toLocaleLowerCase(), undefined, { numeric: true, sensitivity: 'base' });
  }

  // unified getter used by the template
  sortedFilteredDrivers() {
    const rows = Object.values(this.state.data || {}).filter(d =>
      d.name?.toLowerCase().includes(this.state.search.toLowerCase())
    );

    const dir = this.state.sortDir === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      const res = this._compareByKey(a, b, this.state.sortKey);
      if (res !== 0) return dir * res;
      // tie-break by name for stability
      return this._compareByKey(a, b, 'name');
    });

    return rows;
  }

  setup() {
    this.state = useState({
      loading: true,
      metrics: {},
      series: {
        activeDrivers: [], trips: [], supplyHours: [],
        cashEarned: [], moneyPerHour: [], tripsPerHour: [],
        avgSupplyHoursPerDriver: [], utilisation: [], efficiency: [],
        acceptanceRate: [], completedToRequest: [], completionRate: [],
        serviceFee: [], partnerFee: [],
      },
      allDrivers: 0,
      distributions: { product: [], quality: [], category: [] },
      productTypes: [],
      qualityScores: [
        { key: 'Low Performer',     label: 'Low Performer' },
        { key: 'Average Performer', label: 'Average Performer' },
        { key: 'High Performer',    label: 'High Performer' },
      ],
      categories: [],
      timePeriods: ['Last Week','Last Month','Last 3 Months'],
      selectedProducts: [], selectedScores: [], selectedCategories: [], selectedPeriod: 'Last Week',
      startDate: '', endDate: '',
      showProducts: false, showScores: false, showCategories: false, showPeriod: false,
      showCharts: false, search: '', sortKey: 'name', sortDir: 'asc',
    });
    this.chartActive = useRef('chartActive');
    this.chartTrips  = useRef('chartTrips');
    this.chartSupply = useRef('chartSupply');
    this.chartCash   = useRef('chartCash');
    this.chartMoneyPerHour = useRef('chartMoneyPerHour');
    this.chartTripsPerHour = useRef('chartTripsPerHour');
    this.chartAvgSupply = useRef('chartAvgSupply');
    this.chartUtilisation = useRef('chartUtilisation');
    this.chartEfficiency = useRef('chartEfficiency');
    this.chartAcceptance = useRef('chartAcceptance');
    this.chartCompletedToRequest = useRef('chartCompletedToRequest');
    this.chartCompletionRate = useRef('chartCompletionRate');
    this.chartServiceFee = useRef('chartServiceFee');
    this.chartPartnerFee = useRef('chartPartnerFee');
    this.chartQuality  = useRef('chartQuality');
    this.chartProduct  = useRef('chartProduct');
    this.chartCategory = useRef('chartCategory');
    
    this._charts     = {};

    onMounted(async () => {
      const { product_types, categories } = await this.env.services.rpc(
        '/fleet_partner_performance/filters', {}
      );
      this.state.productTypes = product_types;
      this.state.categories   = categories;
      await this._fetchData();
    });

    // whenever the DOM updates, if we're in chart mode, draw the charts
    onPatched(() => {
      if (this.state.showCharts && !this.state.loading) {
        this._renderDistributionCharts();
        this._renderCharts();
      }
    });
  }

  async _fetchData() {
    this.state.loading = true;
    const params = {
      period:     this.state.selectedPeriod,
      products:   this.state.selectedProducts,
      scores:     this.state.selectedScores,
      categories: this.state.selectedCategories,
      start_date: this.state.startDate || undefined,
      end_date:   this.state.endDate   || undefined,
    };
    const resp = await this.env.services.rpc(
      '/fleet_partner_performance/data', params
    );
    this.state.metrics       = resp.metrics;
    this.state.series        = resp.series;
    this.state.data          = resp.data;
    this.state.allDrivers    = resp.allDrivers;
    this.state.distributions = resp.distributions;
    this.state.loading       = false;
    // onPatched() will run next and draw charts if needed
  }

  _renderDistributionCharts() {
    const makePie = (ctx, title, dist, colors, chartKey) => {
      if (this._charts[chartKey]) this._charts[chartKey].destroy();
      const labels = (dist || []).map(d => d.label);
      const data   = (dist || []).map(d => d.value);

      this._charts[chartKey] = new Chart(ctx, {
        type: 'pie',
        data: {
          labels,
          datasets: [{
            label: title,
            data,
            backgroundColor: (colors || []).slice(0, labels.length),
          }],
        },
        options: {
          maintainAspectRatio: false,
          plugins: {
            title: { display: true, text: title, padding: { top: 6, bottom: 6 } },
            legend: { position: 'right', labels: { boxWidth: 12, padding: 12 } },
            tooltip: {
              callbacks: {
                label: (c) => {
                  const val   = c.raw ?? 0;
                  const total = c.dataset.data.reduce((a, b) => a + b, 0) || 1;
                  const pct   = ((val / total) * 100).toFixed(1);
                  return `${c.label}: ${val} (${pct}%)`;
                },
              },
            },
          },
        },
      });
    };

    const { product = [], quality = [], category = [] } = this.state.distributions || {};

    const prodColors = ['#FF6384','#36A2EB','#FFCE56','#8E44AD','#2ECC71','#E67E22'];
    const qualColors = ['#4BC0C0','#9966FF','#FF9F40'];
    const catColors  = ['#E7E9ED','#3CBA9F','#F7464A','#46F0F0','#F39C12','#7F8C8D'];

    const qCtx = this.chartQuality.el?.getContext('2d');
    const pCtx = this.chartProduct.el?.getContext('2d');
    const cCtx = this.chartCategory.el?.getContext('2d');
    if (qCtx) makePie(qCtx, 'Drivers by Quality',  quality,  qualColors, 'qualityPie');
    if (pCtx) makePie(pCtx, 'Drivers by Product',  product,  prodColors, 'productPie');
    if (cCtx) makePie(cCtx, 'Drivers by Category', category, catColors,  'categoryPie');
  }

  _renderCharts() {
    const cfg = (label, data) => {
      // helper to show "DD-MMM" from a few common date-ish strings
      const formatDMmm = (input) => {
        if (!input) return input;
        const s = String(input);

        // ISO like 2025-09-15 or 2025-09-15T...
        const iso = /^(\d{4})-(\d{2})-(\d{2})/;
        // Slash like 15/09/25 or 15/09/2025 (DD/MM/YY)
        const slash = /^(\d{1,2})\/(\d{1,2})\/(\d{2,4})/;

        let d, m, y;

        if (iso.test(s)) {
          const [, Y, M, D] = s.match(iso);
          d = +D; m = +M; y = +Y;
        } else if (slash.test(s)) {
          const [, D, M, Y] = s.match(slash);
          d = +D; m = +M; y = +Y;
          if (y < 100) y += 2000;
        } else {
          const t = Date.parse(s);
          if (!Number.isNaN(t)) {
            const dt = new Date(t);
            d = dt.getDate(); m = dt.getMonth() + 1; y = dt.getFullYear();
          } else {
            return s; // fallback unchanged
          }
        }

        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return `${String(d).padStart(2,'0')}-${months[m-1]}`;
      };

      return {
        type: 'line',
        data: {
          labels: data.map(pt => pt.period),
          datasets: [{ label, data: data.map(pt => pt.value), fill: false }],
        },
        options: {
          // Hide gridlines on both axes
          scales: {
            x: {
              display: true,
              grid: { display: false, drawBorder: false },
              ticks: {
                autoSkip: true,
                maxTicksLimit: 8,
                // Use a function (not arrow) so `this` is the scale
                callback: function(value) {
                  const raw = this.getLabelForValue ? this.getLabelForValue(value) : value;
                  return formatDMmm(raw);
                },
              },
            },
            y: {
              display: true,
              grid: { display: false, drawBorder: true },
              ticks: { autoSkip: true, maxTicksLimit: 6 },
            },
          },
          plugins: {
            title: { display: true, text: label },
            legend: { display: false },
            // (optional) make tooltip title match axis format:
            tooltip: {
              callbacks: {
                title: (items) => items?.length ? formatDMmm(items[0].label) : '',
              },
            },
          },
          elements: {
            point: { radius: 2, hitRadius: 8 },
          },
          maintainAspectRatio: false,
        },
      };
    };

    const ctxA = this.chartActive.el.getContext('2d');
    const ctxT = this.chartTrips.el.getContext('2d');
    const ctxS = this.chartSupply.el.getContext('2d');
    const ctxC = this.chartCash.el.getContext('2d');
    const ctxM = this.chartMoneyPerHour.el.getContext('2d');
    const ctxTP = this.chartTripsPerHour.el.getContext('2d');
    const ctxAS = this.chartAvgSupply.el.getContext('2d');
    const ctxU = this.chartUtilisation.el.getContext('2d');
    const ctxE = this.chartEfficiency.el.getContext('2d');
    const ctxAcc = this.chartAcceptance.el.getContext('2d');
    const ctxCTR = this.chartCompletedToRequest.el.getContext('2d');
    const ctxCRate = this.chartCompletionRate.el.getContext('2d');
    const ctxSF = this.chartServiceFee.el.getContext('2d');
    const ctxPF = this.chartPartnerFee.el.getContext('2d');

    ['active','trips','supply','cash','moneyPerHour','tripsPerHour','avgSupplyHoursPerDriver', 'utilisation', 'efficiency','acceptanceRate','completedToRequest','completionRate','serviceFee','partnerFee'].forEach(k => {
      if (this._charts[k]) { this._charts[k].destroy(); }
    });
    this._charts.active = new Chart(ctxA, cfg('Active Drivers', this.state.series.activeDrivers));
    this._charts.trips  = new Chart(ctxT, cfg('Total Trips',    this.state.series.trips));
    this._charts.supply = new Chart(ctxS, cfg('Supply Hours',   this.state.series.supplyHours));
    this._charts.cash   = new Chart(ctxC, cfg('Gross Revenue',   this.state.series.cashEarned));
    this._charts.moneyPerHour = new Chart(ctxM, cfg('Revenue / Hour',   this.state.series.moneyPerHour));
    this._charts.tripsPerHour = new Chart(ctxTP, cfg('Trips / Hour', this.state.series.tripsPerHour));
    this._charts.avgSupplyHoursPerDriver = new Chart(ctxAS, cfg('SH per Active Driver', this.state.series.avgSupplyHoursPerDriver));
    this._charts.utilisation = new Chart(ctxU ,cfg('Utilisation %', this.state.series.utilisation));
    this._charts.efficiency = new Chart(ctxE ,cfg('Efficiency %', this.state.series.efficiency));
    this._charts.acceptanceRate = new Chart(ctxAcc,cfg('Acceptance Rate %', this.state.series.acceptanceRate));
    this._charts.completedToRequest = new Chart(ctxCTR,cfg('Completed to Request %', this.state.series.completedToRequest));
    this._charts.completionRate = new Chart(ctxCRate,cfg('Completion Rate %', this.state.series.completionRate));

    this._charts.serviceFee = new Chart(ctxSF,cfg('Service Fee', this.state.series.serviceFee));
    this._charts.partnerFee = new Chart(ctxPF,cfg('Partner Fee', this.state.series.partnerFee));
  }

  toggleFilter(listName, value) {
    const list = this.state[listName];
    const idx = list.indexOf(value);
    if (idx === -1) list.push(value);
    else            list.splice(idx, 1);
    this._fetchData();
  }
  changePeriod(p) {
    this.state.selectedPeriod = p;
    this.state.showPeriod    = false;
    this._fetchData();
  }
  toggleDropdown(f) { this.state[f] = !this.state[f]; }
  toggleView()      { this.state.showCharts = !this.state.showCharts; }
  onStartDateChange(ev) {
    this.state.startDate = ev.target.value;
    if (this.state.startDate && this.state.endDate) this._fetchData();
  }
  onEndDateChange(ev) {
    this.state.endDate = ev.target.value;
    if (this.state.startDate && this.state.endDate) this._fetchData();
  }
}

OwlPerformanceDashboard.template = 'owl.OwlPerformanceDashboard';
registry.category('actions')
        .add('owl.performance_dashboard', OwlPerformanceDashboard);
