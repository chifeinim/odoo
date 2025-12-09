/** @odoo-module **/

const { Component, hooks } = owl;
const { useState, useRef, onMounted, onWillUnmount } = hooks;
import { registry } from '@web/core/registry';

export class OwlCallCenterDashboard extends Component {

  formatNumber(v, d = 0) {
    const n = Number(v);
    if (Number.isNaN(n)) return v ?? '';
    return new Intl.NumberFormat('en-GB', {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    }).format(n);
  }

  formatDateYMDToDMY(ymd) {
    if (!ymd) return '';
    const [y,m,d] = ymd.split('-').map(Number);
    if (!y || !m || !d) return ymd;
    return `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${y}`;
  }

  setup() {
    this.state = useState({
      loading: true,
      filters: {
        productTypes: [],
        categories: [],
        issueTypes: [],
        statuses: [],
        timePeriods: [],
      },

      // filter values
      selectedProducts: [],
      selectedCategories: [],
      selectedIssueTypes: [],
      selectedStatuses: [],
      selectedPeriod: 'Last Week',
      startDate: '',
      endDate: '',

      drivers: [],

      // UI bits
      search: '',
      sortKey: 'issues_total',
      sortDir: 'desc',

      // Driver detail modal
      driverModalOpen: false,
      driverDetail: null,   // { driver, cards, series, issues, metrics_range }

      // Issue detail modal
      issueModalOpen: false,
      issueDetail: null,    // { issue, call_notes }

      // Status change modal
      statusModalOpen: false,
      statusModal: {
        issue_id: null,
        current_status: '',
        current_status_label: '',
        new_status: '',
        note: '',
      },
    });

    this.driverModalRef = useRef('driverModal');
    this.issueModalRef  = useRef('issueModal');
    this.statusModalRef = useRef('statusModal');

    onMounted(async () => {
      const f = await this.env.services.rpc('/fleet_call_center/filters', {});
      this.state.filters.productTypes = f.product_types || [];
      this.state.filters.categories   = f.categories || [];
      this.state.filters.issueTypes   = f.issue_types || [];
      this.state.filters.statuses     = f.statuses || [];
      this.state.filters.timePeriods  = f.time_periods || [];

      if (!this.state.selectedPeriod && this.state.filters.timePeriods.length) {
        this.state.selectedPeriod = this.state.filters.timePeriods[0];
      }

      await this._fetchBoard();
      this.state.loading = false;

      this._onKeyUp = (ev) => {
        if (ev.key === 'Escape') {
          if (this.state.statusModalOpen) this.closeStatusModal();
          else if (this.state.issueModalOpen) this.closeIssueModal();
          else if (this.state.driverModalOpen) this.closeDriverModal();
        }
      };
      window.addEventListener('keyup', this._onKeyUp);
    });

    onWillUnmount(() => {
      if (this._onKeyUp) {
        window.removeEventListener('keyup', this._onKeyUp);
      }
    });
  }

  async _fetchBoard() {
    this.state.loading = true;
    const params = {
      period: this.state.selectedPeriod,
      products: this.state.selectedProducts,
      categories: this.state.selectedCategories,
      issue_types: this.state.selectedIssueTypes,
      statuses: this.state.selectedStatuses,
      start_date: this.state.startDate || undefined,
      end_date: this.state.endDate || undefined,
    };
    const resp = await this.env.services.rpc('/fleet_call_center/data', params);
    this.state.drivers = resp.drivers || [];
    if (resp.range) {
      this.state.startDate = resp.range.start || this.state.startDate;
      this.state.endDate   = resp.range.end   || this.state.endDate;
    }
    this.state.loading = false;
  }

  // ---- sorting / filtering on board ----------------------------------

  _compare(a, b, key) {
    const ax = a?.[key], bx = b?.[key];

    const norm = (v) => (v === undefined || v === null)
      ? null
      : (typeof v === 'boolean' ? (v ? 1 : 0)
      : (typeof v === 'number' ? v
      : String(v).toLowerCase()));

    const av = norm(ax), bv = norm(bx);

    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;

    if (typeof av === 'number' && typeof bv === 'number') {
      return av - bv;
    }
    return String(av).localeCompare(String(bv), undefined, {
      numeric: true, sensitivity: 'base',
    });
  }

  setSort(key) {
    if (this.state.sortKey === key) {
      this.state.sortDir = this.state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.state.sortKey = key;
      this.state.sortDir = 'asc';
    }
  }

  onBoardSearch(ev) {
    this.state.search = ev.target.value || '';
  }

  boardRows() {
    const q = (this.state.search || '').trim().toLowerCase();
    let rows = this.state.drivers || [];
    if (q) {
      rows = rows.filter(r =>
        (r.name || '').toLowerCase().includes(q) ||
        (r.phone || '').toLowerCase().includes(q)
      );
    }
    const key = this.state.sortKey;
    const dir = this.state.sortDir === 'asc' ? 1 : -1;
    rows = [...rows];
    rows.sort((a, b) => {
      const r = this._compare(a, b, key);
      if (r !== 0) return dir * r;
      return this._compare(a, b, 'name');
    });
    return rows;
  }

  // ---- filters --------------------------------------------------------

  async applyFilters() {
    await this._fetchBoard();
  }

  toggleFilter(listKey, value) {
    const list = this.state[listKey];
    if (!Array.isArray(list)) return;
    const idx = list.indexOf(value);
    if (idx === -1) list.push(value); else list.splice(idx, 1);
  }

  changePeriod(p) {
    this.state.selectedPeriod = p;
    this.state.startDate = '';
    this.state.endDate = '';
  }

  onStartDateChange(ev) {
    this.state.startDate = ev.target.value || '';
    this.state.selectedPeriod = 'Custom';
  }

  onEndDateChange(ev) {
    this.state.endDate = ev.target.value || '';
    this.state.selectedPeriod = 'Custom';
  }

  // ---- driver modal ---------------------------------------------------

  async openDriver(drv) {
    this.state.driverModalOpen = true;
    this.state.driverDetail = null;

    const res = await this.env.services.rpc('/fleet_call_center/driver_detail', {
      driver_id: drv.id,
      start_date: this.state.startDate || this.state.endDate || '',
      end_date: this.state.endDate   || this.state.startDate || '',
    });

    this.state.driverDetail = res;
  }

  closeDriverModal() {
    this.state.driverModalOpen = false;
    this.state.driverDetail = null;
  }

  // ---- issue detail modal --------------------------------------------

  async openIssue(issueRow) {
    this.state.issueModalOpen = true;
    this.state.issueDetail = null;
    const res = await this.env.services.rpc('/fleet_call_center/issue_detail', {
      issue_id: issueRow.id,
    });
    if (res && res.ok) {
      this.state.issueDetail = res;
    } else {
      this.state.issueModalOpen = false;
    }
  }

  closeIssueModal() {
    this.state.issueModalOpen = false;
    this.state.issueDetail = null;
  }

  // ---- status change modal -------------------------------------------

  openStatusModal(issueRow) {
    const d = this.state.driverDetail;
    const statuses = this.state.filters.statuses || [];
    const currentLabel = issueRow.status_label || '';
    this.state.statusModal = {
      issue_id: issueRow.id,
      current_status: issueRow.status,
      current_status_label: currentLabel,
      new_status: issueRow.status,
      note: '',
    };
    this.state.statusModalOpen = true;
  }

  closeStatusModal() {
    this.state.statusModalOpen = false;
  }

  onStatusChange(ev) {
    this.state.statusModal.new_status = ev.target.value;
  }

  onStatusNote(ev) {
    this.state.statusModal.note = ev.target.value || '';
  }

  async submitStatusChange() {
    const payload = {
      issue_id: this.state.statusModal.issue_id,
      new_status: this.state.statusModal.new_status,
      note: this.state.statusModal.note,
    };
    const res = await this.env.services.rpc('/fleet_call_center/update_issue_status', payload);

    if (!res || !res.ok) {
      // You can swap to a proper dialog/notification service later.
      window.alert(res && res.error ? res.error : 'Failed to update status');
      return;
    }

    // Update issue row in driver detail
    const updated = res.issue;
    if (this.state.driverDetail && Array.isArray(this.state.driverDetail.issues)) {
      const arr = this.state.driverDetail.issues;
      const idx = arr.findIndex(i => i.id === updated.id);
      if (idx !== -1) {
        arr[idx] = {
          ...arr[idx],
          status: updated.status,
          status_label: updated.status_label,
          date_resolved: updated.date_resolved || arr[idx].date_resolved,
        };
      }
    }

    // If issue detail modal is open for this issue, refresh it
    if (this.state.issueModalOpen &&
        this.state.issueDetail &&
        this.state.issueDetail.issue &&
        this.state.issueDetail.issue.id === updated.id) {
      await this.openIssue({ id: updated.id });
    }

    this.closeStatusModal();
  }
}

OwlCallCenterDashboard.template = 'owl.OwlCallCenterDashboard';
registry.category('actions').add('owl.call_center_dashboard', OwlCallCenterDashboard);
