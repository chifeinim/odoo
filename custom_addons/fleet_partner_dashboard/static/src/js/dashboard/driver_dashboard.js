/** @odoo-module **/

console.log("✅ OwlDriverDashboard JS is loaded!");

const { Component, hooks } = owl;
const { useState, onMounted } = hooks;
import { registry } from "@web/core/registry";

export class OwlDriverDashboard extends Component {
  setup() {
    this.state = useState({
      loading:       true,
      error:         null,
      windows:       [],
      drivers:       [],
      tagsSummary:   [],
      search:        '',
      selectedTags:  [],
      dropdownOpen:  false,   // ← track menu open/closed
    });

    onMounted(async () => {
      try {
        const { windows, drivers: rawDrivers, tags_summary } =
          await this.env.services.rpc('/fleet_partner_dashboard/data', {});
        this.state.windows     = windows;
        this.state.drivers     = rawDrivers;         // you already sort above
        this.state.tagsSummary = tags_summary;
      } catch (err) {
        this.state.error = err;
      } finally {
        this.state.loading = false;
      }
    });
  }

  // toggle the dropdown menu
  toggleDropdown() {
    this.state.dropdownOpen = !this.state.dropdownOpen;
  }

  // toggle a tag in selectedTags
  toggleTag(tagName) {
    const idx = this.state.selectedTags.indexOf(tagName);
    if (idx === -1) {
      this.state.selectedTags.push(tagName);
    } else {
      this.state.selectedTags.splice(idx, 1);
    }
  }
}

OwlDriverDashboard.template = "owl.OwlDriverDashboard";
registry.category("actions").add("owl.driver_dashboard", OwlDriverDashboard);
