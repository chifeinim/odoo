/** @odoo-module **/

console.log("✅ OwlPerformanceDashboard JS is loaded!");

const { Component, hooks } = owl;
const { useState, onMounted } = hooks;
import { registry } from "@web/core/registry";

export class OwlPerformanceDashboard extends Component {
  setup() {
    this.state = useState({
      loading:            true,
      data:               {},

      // the “options” for each filter
      productTypes:       [],                // [{id,name},…]
      qualityScores:      [                 // fixed set
        { key: 'High Performer',   label: 'High Performer'   },
        { key: 'Average Performer', label: 'Average Performer' },
        { key: 'Low Performer',     label: 'Low Performer'     },
      ],
      categories:         [],                // ['new','active',…]
      timePeriods:        ['Last Week','Last Month','All Time'],

      // what’s currently selected
      selectedProducts:   [],
      selectedScores:     [],
      selectedCategories: [],
      selectedPeriod:     'Last Week',

      // dropdown open/closed flags
      showProducts:       false,
      showScores:         false,
      showCategories:     false,
      showPeriod:         false,

      // **NEW** free‑text search string for drivers
      search:             '',
    });

    onMounted(async () => {
      // load filter options
      const { product_types, categories } = await this.env.services.rpc(
        '/fleet_partner_performance/filters', {}
      );
      this.state.productTypes = product_types;
      this.state.categories   = categories;

      // initial data fetch
      await this._fetchData();
    });
  }

  // fetch (or re‐fetch) the main table + KPI data
  async _fetchData() {
    this.state.loading = true;
    const params = {
      period:     this.state.selectedPeriod,
      products:   this.state.selectedProducts,
      scores:     this.state.selectedScores,
      categories: this.state.selectedCategories,
    };
    this.state.data = await this.env.services.rpc(
      '/fleet_partner_performance/data',
      params
    );
    this.state.loading = false;
  }

  // toggle a value in any of the multi‑select lists
  toggleFilter(listName, value) {
    const list = this.state[listName];
    const idx = list.indexOf(value);
    if (idx === -1) {
      list.push(value);
    } else {
      list.splice(idx, 1);
    }
    this._fetchData();
  }

  // change the time period (single‑select)
  changePeriod(period) {
    this.state.selectedPeriod = period;
    this.state.showPeriod = false;
    this._fetchData();
  }

  // toggle any of the dropdown menus by its flag name
  toggleDropdown(flagName) {
    this.state[flagName] = !this.state[flagName];
  }
}

OwlPerformanceDashboard.template = "owl.OwlPerformanceDashboard";
registry
  .category("actions")
  .add("owl.performance_dashboard", OwlPerformanceDashboard);
