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
      search:  '',          // ← add this
    });

    onMounted(async () => {
      try {
        const { windows, drivers } = await this.env.services.rpc(
          '/fleet_partner_dashboard/data',
          {}
        );
        this.state.windows = windows;
        this.state.drivers = drivers;
      } catch (err) {
        this.state.error = err;
      } finally {
        this.state.loading = false;
      }
    });
  }
}

OwlDriverDashboard.template = "owl.OwlDriverDashboard";
registry.category("actions").add("owl.driver_dashboard", OwlDriverDashboard);
