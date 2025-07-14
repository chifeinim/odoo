/** @odoo-module **/

console.log("✅ OwlDriverDashboard JS is loaded!");

// Pull Component and hooks off the global `owl`
const { Component, hooks } = owl;
const { useState, onMounted } = hooks;

import { registry } from "@web/core/registry";

export class OwlDriverDashboard extends Component {
  setup() {
    this.state = useState({
      loading: true,
      error:   null,
      windows: [],
      drivers: [],
      tagsSummary: [],
      search:  '',
    });

    onMounted(async () => {
    try {
        // Destructure windows, drivers, AND tags_summary from your JSON
        const { windows, drivers: rawDrivers, tags_summary } =
        await this.env.services.rpc(
            '/fleet_partner_dashboard/data',
            {}
        );
        this.state.windows = windows;

        // Sort drivers as before...
        const sorted = rawDrivers.sort((a, b) => {
        const a7 = a.periods[0].tags.length,  b7 = b.periods[0].tags.length;
        if (b7 !== a7)  return b7 - a7;
        const a30 = a.periods[1].tags.length, b30 = b.periods[1].tags.length;
        if (b30 !== a30) return b30 - a30;
        return a.name.localeCompare(b.name);
        });
        this.state.drivers     = sorted;

        // Now assign the tag summary you just destructured
        this.state.tagsSummary = tags_summary;
    } catch (err) {
        this.state.error = err;
        console.error("❌ Dashboard data error", err);
    } finally {
        this.state.loading = false;
    }
    });

  }
}

OwlDriverDashboard.template = "owl.OwlDriverDashboard";
registry.category("actions").add("owl.driver_dashboard", OwlDriverDashboard);
