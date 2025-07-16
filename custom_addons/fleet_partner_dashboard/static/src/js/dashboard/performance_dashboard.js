/** @odoo-module **/

console.log("✅ OwlPerformanceDashboard JS is loaded!");

const { Component, hooks } = owl;
// pull just the two hooks you need:
const { useState, onMounted } = hooks;

import { registry } from "@web/core/registry";

export class OwlPerformanceDashboard extends Component {
  setup() {
    // initialize reactive state
    this.state = useState({ loading: true, data: {} });

    // run this once the component is mounted
    onMounted(async () => {
      // fetch your performance data
      this.state.data = await this.env.services.rpc(
        '/fleet_partner_performance/data', {}
      );
      this.state.loading = false;
    });
  }
}

// point at your QWeb template
OwlPerformanceDashboard.template = "owl.OwlPerformanceDashboard";

// register your client‑action
registry.category("actions")
        .add("owl.performance_dashboard", OwlPerformanceDashboard);
