/** @odoo-module **/

const { Component, hooks } = owl;
const { onMounted, onPatched, useState, useRef } = hooks;
import { registry } from '@web/core/registry';

export class OwlPerformanceDashboard extends Component {
  setup() {
    this.state = useState({
      loading: true,
      metrics: {},
      series: { activeDrivers: [], trips: [], supplyHours: [], cashEarned: [], moneyPerHour: [], tripsPerHour: [] },
      productTypes: [],
      qualityScores: [
        { key: 'Low Performer',     label: 'Low Performer' },
        { key: 'Average Performer', label: 'Average Performer' },
        { key: 'High Performer',    label: 'High Performer' },
      ],
      categories: [],
      timePeriods: ['Last Week','Last Month','Last 3 Months','All Time'],
      selectedProducts: [], selectedScores: [], selectedCategories: [], selectedPeriod: 'Last Week',
      startDate: '', endDate: '',
      showProducts: false, showScores: false, showCategories: false, showPeriod: false,
      showCharts: false, search: '',
    });
    this.chartActive = useRef('chartActive');
    this.chartTrips  = useRef('chartTrips');
    this.chartSupply = useRef('chartSupply');
    this.chartCash   = useRef('chartCash');
    this.chartMoneyPerHour = useRef('chartMoneyPerHour');
    this.chartTripsPerHour = useRef('chartTripsPerHour');
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
    this.state.metrics = resp.metrics;
    this.state.series  = resp.series;
    this.state.data    = resp.data;
    this.state.loading = false;
    // onPatched() will run next and draw charts if needed
  }

  _renderCharts() {
    const cfg = (label, data) => ({
      type: 'line',
      data: {
        labels: data.map(pt => pt.period),
        datasets: [{ label, data: data.map(pt => pt.value), fill: false }],
      },
      options: {
        scales: { x: { display: true }, y: { display: true } },
        plugins: {
          title: { display: true, text: label },
          legend: { display: false },
        },
      },
    });
    const ctxA = this.chartActive.el.getContext('2d');
    const ctxT = this.chartTrips.el.getContext('2d');
    const ctxS = this.chartSupply.el.getContext('2d');
    const ctxC = this.chartCash.el.getContext('2d');
    const ctxM = this.chartMoneyPerHour.el.getContext('2d');
    const ctxTP = this.chartTripsPerHour.el.getContext('2d');

    ['active','trips','supply','cash','moneyPerHour','tripsPerHour'].forEach(k => {
      if (this._charts[k]) { this._charts[k].destroy(); }
    });
    this._charts.active = new Chart(ctxA, cfg('Active Drivers', this.state.series.activeDrivers));
    this._charts.trips  = new Chart(ctxT, cfg('Total Trips',    this.state.series.trips));
    this._charts.supply = new Chart(ctxS, cfg('Supply Hours',   this.state.series.supplyHours));
    this._charts.cash   = new Chart(ctxC, cfg('Cash Earned',   this.state.series.cashEarned));
    this._charts.moneyPerHour = new Chart(ctxM, cfg('Avg Money / Hour',   this.state.series.moneyPerHour));
    this._charts.tripsPerHour = new Chart(ctxTP, cfg('Avg Trips / Hour', this.state.series.tripsPerHour));
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
