(function () {
  "use strict";
  const POLL_MS = 1500;
  let refreshRunning = false;
  let refreshTimer = null;

  function esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"
    }[c]));
  }

  function showMessage(message, type = "success") {
    const box = document.getElementById("live-message");
    if (!box) return;
    const item = document.createElement("div");
    item.className = `flash flash-${type}`;
    item.textContent = message;
    box.replaceChildren(item);
    window.setTimeout(() => item.remove(), 3600);
  }

  function setSyncStatus(text, ok = true) {
    document.querySelectorAll(".live-status").forEach(el => {
      el.classList.toggle("offline", !ok);
      el.innerHTML = `<span class="live-dot"></span>${esc(text)}`;
    });
  }

  async function submitAjax(form) {
    const button = form.querySelector("button[type=submit],button:not([type])");
    const oldText = button ? button.textContent : "";
    if (button) { button.disabled = true; button.textContent = "Working…"; }
    try {
      const response = await fetch(form.action, {
        method: (form.method || "POST").toUpperCase(),
        body: new FormData(form),
        headers: {"X-Requested-With":"XMLHttpRequest", "Accept":"application/json"},
        cache: "no-store"
      });
      const data = await response.json().catch(() => ({ok:false,message:"Unexpected server response."}));
      if (!response.ok || !data.ok) {
        showMessage(data.message || `Request failed (${response.status}).`, "danger");
        return;
      }
      showMessage(data.message || "Saved successfully.", "success");
      await refreshAll(true);
      const redirect = form.dataset.redirect;
      if (redirect) window.location.href = redirect;
    } catch (e) {
      showMessage("Could not connect to the server. Please try again.", "danger");
      setSyncStatus("Connection problem", false);
    } finally {
      if (button) { button.disabled = false; button.textContent = oldText; }
    }
  }

  document.addEventListener("submit", e => {
    const form = e.target;
    if (form.matches("[data-ajax='true']")) {
      e.preventDefault();
      submitAjax(form);
    }
  });

  document.addEventListener("click", e => {
    const button = e.target.closest("[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) e.preventDefault();
  });

  function statsHtml(stats, labels) {
    return `<div class="stats-grid">${Object.entries(labels).map(([key,label]) =>
      `<div class="stat-card ${key === "overdue" && Number(stats[key]) > 0 ? "stat-card-warning" : ""}">
        <span class="stat-value">${esc(stats[key])}</span><span class="stat-label">${esc(label)}</span>
      </div>`).join("")}</div>`;
  }

  function issueRows(rows, showStudent) {
    if (!rows.length) return `<p class="empty-state">No active loans.</p>`;
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Book</th>${showStudent?"<th>Student</th>":""}<th>Issued</th><th>Due</th><th>Status</th><th>Action</th></tr></thead><tbody>${rows.map(r =>
      `<tr><td><strong>${esc(r.book)}</strong></td>${showStudent?`<td>${esc(r.student)}</td>`:""}<td>${esc(r.issued)}</td><td>${esc(r.due)}</td>
      <td>${r.overdue?'<span class="badge badge-danger">Overdue</span>':'<span class="badge badge-info">Issued</span>'}</td>
      <td>${showStudent?`<form method="POST" action="/return/${r.id}" data-ajax="true" class="inline-form"><button class="btn btn-sm btn-outline">Mark Returned</button></form>`:"—"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function requestsHtml(rows, staff) {
    if (!rows.length) return `<p class="empty-state">No ${staff?"pending ":""}book requests.</p>`;
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Book</th><th>Student</th><th>Requested</th><th>Available</th><th>Status</th><th>Action</th></tr></thead><tbody>${rows.map(r => {
      const statusClass = r.status === "approved" ? "badge-success" : (r.status === "rejected" || r.status === "cancelled" ? "badge-danger" : "badge-warning");
      const action = staff && r.status === "pending"
        ? `<form method="POST" action="/requests/${r.id}/approve" data-ajax="true" class="inline-form"><button class="btn btn-sm btn-primary">Approve</button></form><form method="POST" action="/requests/${r.id}/reject" data-ajax="true" class="inline-form"><button class="btn btn-sm btn-danger">Reject</button></form>`
        : (!staff && r.status === "pending" ? `<form method="POST" action="/requests/${r.id}/cancel" data-ajax="true" class="inline-form"><button class="btn btn-sm btn-outline">Cancel</button></form>` : "—");
      return `<tr><td><strong>${esc(r.book)}</strong></td><td>${esc(r.student)}</td><td>${esc(r.requested_at)}</td><td>${esc(r.available)}</td><td><span class="badge ${statusClass}">${esc(r.status)}</span></td><td class="actions-cell">${action}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  async function getJson(url) {
    const res = await fetch(url, {cache:"no-store", headers:{"X-Requested-With":"XMLHttpRequest", "Accept":"application/json"}});
    if (!res.ok) throw new Error(String(res.status));
    return await res.json();
  }

  async function refreshDashboard() {
    const target = document.getElementById("dashboard-live");
    if (!target) return;
    const d = await getJson(target.dataset.liveUrl);
    if (d.role === "admin") {
      target.innerHTML = statsHtml(d.stats,{total_books:"Book Titles",total_copies:"Total Copies",books_issued:"Currently Issued",overdue:"Overdue",total_librarians:"Librarians",total_students:"Students"}) +
        `<section class="panel"><div class="section-heading"><h2>Recent Issue Activity</h2><span class="mini-live">LIVE</span></div>${issueRows(d.recent,false)}</section>`;
    } else if (d.role === "librarian") {
      target.innerHTML = statsHtml(d.stats,{total_books:"Book Titles",books_issued:"Currently Issued",overdue:"Overdue",total_students:"Students"}) +
        `<section class="panel"><div class="section-heading"><h2>Pending Student Requests</h2><span class="mini-live">LIVE</span></div>${requestsHtml(d.requests,true)}</section>` +
        `<section class="panel"><div class="section-heading"><h2>Active Loans</h2><span class="mini-live">LIVE</span></div>${issueRows(d.active,true)}</section>`;
    } else {
      target.innerHTML = statsHtml(d.stats,{borrowed:"Books Currently Borrowed",history:"Total Loans",titles:"Titles in Catalog"}) +
        `<section class="panel"><div class="section-heading"><h2>My Book Requests</h2><span class="mini-live">LIVE</span></div>${requestsHtml(d.requests,false)}</section>` +
        `<section class="panel"><div class="section-heading"><h2>Currently Borrowed</h2><span class="mini-live">LIVE</span></div>${issueRows(d.active,false)}</section>`;
    }
  }

  async function refreshBooks() {
    const target = document.getElementById("books-live");
    if (!target) return;
    const search = document.querySelector("#book-search input[name=q]");
    const url = target.dataset.liveUrl + (search && search.value ? "?q="+encodeURIComponent(search.value) : "");
    const d = await getJson(url);
    const role = document.body.dataset.role;
    const staff = role === "admin" || role === "librarian";
    let html = `<div class="live-status"><span class="live-dot"></span>Live • auto-updating</div>`;
    if (!d.books.length) { target.innerHTML = html + `<p class="empty-state">No books found.</p>`; return; }
    html += `<div class="table-wrap"><table class="data-table"><thead><tr><th>Title</th><th>Author</th><th>Category</th><th>ISBN</th><th>Available</th><th>${staff?"Actions":"Request"}</th></tr></thead><tbody>`;
    d.books.forEach(b => {
      let action = "";
      if (staff) action = `<a class="btn btn-sm btn-outline" href="/books/edit/${b.id}">Edit</a><form method="POST" action="/books/delete/${b.id}" data-ajax="true" class="inline-form"><button class="btn btn-sm btn-danger" data-confirm="Delete this book?">Delete</button></form>`;
      else if (b.pending_request) action = `<span class="badge badge-warning">Requested</span>`;
      else if (b.available <= 0) action = `<span class="badge badge-muted">Unavailable</span><form method="POST" action="/request-book/${b.id}" data-ajax="true" class="inline-form"><button class="btn btn-sm btn-outline">Request</button></form>`;
      else action = `<form method="POST" action="/request-book/${b.id}" data-ajax="true"><button class="btn btn-sm btn-primary">Request</button></form>`;
      if (!staff && b.pending_request) action = `<span class="badge badge-warning">Requested</span>`;
      html += `<tr><td><strong>${esc(b.title)}</strong></td><td>${esc(b.author)}</td><td>${esc(b.category||"—")}</td><td>${esc(b.isbn||"—")}</td><td>${b.available>0?`<span class="badge badge-success">${b.available} / ${b.quantity}</span>`:`<span class="badge badge-danger">0 / ${b.quantity}</span>`}</td><td class="actions-cell">${action}</td></tr>`;
    });
    target.innerHTML = html + `</tbody></table></div>`;
  }

  async function refreshIssueSelect() {
    const select = document.getElementById("issue-book-select");
    if (!select) return;
    const d = await getJson("/api/books");
    const selected = select.value;
    select.innerHTML = `<option value="">Select a book</option>` + d.books.filter(b=>b.available>0).map(b => `<option value="${b.id}">${esc(b.title)} — ${esc(b.author)} (${b.available} available)</option>`).join("");
    if (d.books.some(b=>String(b.id)===String(selected) && b.available>0)) select.value=selected;
  }

  async function refreshAll(force = false) {
    if (refreshRunning && !force) return;
    if (document.visibilityState === "hidden" && !force) return;
    refreshRunning = true;
    try {
      await Promise.all([refreshDashboard(), refreshBooks(), refreshIssueSelect()]);
      setSyncStatus(`Live • ${new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"})}`, true);
    } catch (_) {
      setSyncStatus("Live connection retrying…", false);
    } finally {
      refreshRunning = false;
    }
  }

  function schedule() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => { await refreshAll(false); schedule(); }, POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    document.body.dataset.role = document.querySelector(".user-badge em")?.textContent.replace(/[()]/g, "") || "";
    await refreshAll(true);
    schedule();
  });
})();
