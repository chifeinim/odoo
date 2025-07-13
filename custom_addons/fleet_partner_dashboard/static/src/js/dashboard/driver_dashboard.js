/** @odoo-module **/

console.log("✅ OwlDriverDashboard JS is loaded!");

// Use the global `owl` object (already on the page)
const { Component } = owl;

// Import the client‐action registry
import { registry } from "@web/core/registry";

export class OwlDriverDashboard extends Component {
  setup() {
    // Here you can initialize state, fetch data, etc.
  }
}

// Point to your QWeb template
OwlDriverDashboard.template = "owl.OwlDriverDashboard";

// Register under the same tag as in your ir.actions.client
registry.category("actions").add("owl.driver_dashboard", OwlDriverDashboard);
