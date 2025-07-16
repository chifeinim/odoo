/** @odoo-module **/

console.log("✅ OwlPerformanceDashboard JS is loaded!");

const { Component, hooks } = owl;
const { useState, onMounted } = hooks;
import { registry } from "@web/core/registry";

export class OwlPerformanceDashboard extends Component {
  setup() {
    // reactive state including filters
    this.state = useState({
      loading: true,
      data: {},
      productTypes: [],       // to be filled on mount
      qualityScores: [        // fixed mapping
        { key: 'High Performer', label: 'High Performer' },
        { key: 'Average Performer', label: 'Average Performer' },
        { key: 'Low Performer', label: 'Low Performer' },
      ],
      categories: [],         // driver.type options
      timePeriods: ['Last Week', 'Last Month', 'All Time'],
      // selected filters
      selectedProducts: [],
      selectedScores: [],
      selectedCategories: [],
      selectedPeriod: 'Last Week',
      // control dropdown visibility
      showProducts: false,
      showScores: false,
      showCategories: false,
    });

    onMounted(async () => {
      // 1) fetch filter options (product types & driver categories)
      const res = await this.env.services.rpc('/fleet_partner_performance/filters', {});
      this.state.productTypes = res.product_types;   // [{id,name},…]
      this.state.categories   = res.categories;      // ['new','active',…]
      // 2) fetch table data
      await this._fetchData();
    });
  }

  // fetch or re‑fetch with current filter state
  async _fetchData() {
    this.state.loading = true;
    const params = {
      period:     this.state.selectedPeriod,
      products:   this.state.selectedProducts,
      scores:     this.state.selectedScores,
      categories: this.state.selectedCategories,
    };
    this.state.data = await this.env.services.rpc(
      '/fleet_partner_performance/data', params
    );
    this.state.loading = false;
  }

  // generic toggle for multi‑select dropdowns
  toggleFilter(listName, value) {
    const sel = this.state[listName];
    const i = sel.indexOf(value);
    if (i === -1) sel.push(value);
    else sel.splice(i, 1);
    this._fetchData();
  }

  // change period
  onPeriodChange(ev) {
    this.state.selectedPeriod = ev.target.value;
    this._fetchData();
  }

  // toggle dropdown visibility
  toggleDropdown(name) {
    this.state[name] = !this.state[name];
  }
}

OwlPerformanceDashboard.template = "owl.OwlPerformanceDashboard";
registry
  .category("actions")
  .add("owl.performance_dashboard", OwlPerformanceDashboard);
