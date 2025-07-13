/** @odoo-module **/

import { Component, useState, onWillStart, mount } from "@odoo/owl";
import { jsonrpc } from "@web/core/network/rpc_service";

class DriverDashboard extends Component {
    setup() {
        this.state = useState({ data: {} });

        onWillStart(async () => {
            try {
                const result = await jsonrpc("/fleet_dashboard/data");
                this.state.data = result || {};
            } catch (error) {
                console.error("Failed to load dashboard data:", error);
                this.state.data = {};
            }
        });
    }

    static template = "fleet_partner_dashboard.DriverDashboard";
}

console.log("DriverDashboard script loaded");

document.addEventListener("DOMContentLoaded", () => {
    const mountPoint = document.getElementById("fleet_dashboard_root");
    if (mountPoint) {
        console.log("Mount point found, mounting component");
        mount(DriverDashboard, { target: mountPoint });
    } else {
        console.warn("Mount point #fleet_dashboard_root not found");
    }
});

