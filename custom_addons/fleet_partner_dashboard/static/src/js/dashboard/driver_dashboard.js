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
      catsSummary:   [],
      search:        '',
      selectedCats:  [],    // holds labels now
      dropdownOpen:  false,
    });

    onMounted(async () => {
      try {
        const { windows, drivers: rawDrivers, catsSummary } =
          await this.env.services.rpc('/fleet_partner_dashboard/data', {});
        this.state.windows     = windows;
        this.state.drivers     = rawDrivers;
        this.state.catsSummary = catsSummary;
      } catch (err) {
        this.state.error = err;
      } finally {
        this.state.loading = false;
      }
    });
  }

  toggleDropdown() {
    this.state.dropdownOpen = !this.state.dropdownOpen;
  }

  toggleCategory(catLabel) {
    const idx = this.state.selectedCats.indexOf(catLabel);
    if (idx === -1) {
      this.state.selectedCats.push(catLabel);
    } else {
      this.state.selectedCats.splice(idx, 1);
    }
  }
}

OwlDriverDashboard.template = 'owl.OwlDriverDashboard';
registry.category('actions').add('owl.driver_dashboard', OwlDriverDashboard);
