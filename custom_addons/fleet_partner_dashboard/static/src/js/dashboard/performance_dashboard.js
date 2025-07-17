/** @odoo-module **/

console.log("✅ OwlPerformanceDashboard JS is loaded!");

const { Component, hooks } = owl;
const { useState, onMounted } = hooks;
import { registry } from "@web/core/registry";

export class OwlPerformanceDashboard extends Component {
  setup() {
    this.state = useState({
      loading:          true,
      data:             {},
      productTypes:     [],
      qualityScores: [
        { key: "High Performer",   label: "High Performer"   },
        { key: "Average Performer", label: "Average Performer" },
        { key: "Low Performer",     label: "Low Performer"     }
      ],
      categories:       [],
      timePeriods:      ["Last Week", "Last Month", "All Time"],
      selectedProducts:   [],
      selectedScores:     [],
      selectedCategories: [],
      selectedPeriod:     "Last Week",
      // ← our new date fields
      startDate:        "",
      endDate:          "",
      showProducts:     false,
      showScores:       false,
      showCategories:   false,
      showPeriod:       false,
      search:           ""
    });

    onMounted(async () => {
      const { product_types, categories } =
        await this.env.services.rpc("/fleet_partner_performance/filters", {});
      this.state.productTypes = product_types;
      this.state.categories   = categories;
      await this._fetchData();
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
      end_date:   this.state.endDate   || undefined
    };
    this.state.data = await this.env.services.rpc(
      "/fleet_partner_performance/data",
      params
    );
    this.state.loading = false;
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

  toggleDropdown(flag) {
    this.state[flag] = !this.state[flag];
  }

  onStartDateChange(ev) {
    this.state.startDate = ev.target.value;
    if (this.state.startDate && this.state.endDate) {
      this._fetchData();
    }
  }
  onEndDateChange(ev) {
    this.state.endDate = ev.target.value;
    if (this.state.startDate && this.state.endDate) {
      this._fetchData();
    }
  }
}

OwlPerformanceDashboard.template = "owl.OwlPerformanceDashboard";
registry
  .category("actions")
  .add("owl.performance_dashboard", OwlPerformanceDashboard);
