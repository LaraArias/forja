const API = 'http://localhost:8000';
let currentFilter = 'all';

function getToken() { return localStorage.getItem('token'); }
function setToken(t) { localStorage.setItem('token', t); }

function headers(json) {
    const h = { 'Authorization': 'Bearer ' + getToken() };
    if (json) h['Content-Type'] = 'application/json';
    return h;
}

// Auth
function showRegister() {
    document.getElementById('login-form').style.display = 'none';
    document.getElementById('register-form').style.display = 'block';
    document.getElementById('auth-error').textContent = '';
}
function showLogin() {
    document.getElementById('register-form').style.display = 'none';
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('auth-error').textContent = '';
}

async function register() {
    const email = document.getElementById('reg-email').value;
    const password = document.getElementById('reg-password').value;
    const res = await fetch(API + '/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
        const d = await res.json();
        document.getElementById('auth-error').textContent = d.detail || 'Registration failed';
        return;
    }
    showLogin();
    document.getElementById('login-email').value = email;
    document.getElementById('login-password').value = password;
}

async function login() {
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    const res = await fetch(API + '/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
        document.getElementById('auth-error').textContent = 'Invalid credentials';
        return;
    }
    const data = await res.json();
    setToken(data.access_token);
    showDashboard();
}

function logout() {
    localStorage.removeItem('token');
    document.getElementById('dashboard-section').style.display = 'none';
    document.getElementById('auth-section').style.display = 'block';
}

function showDashboard() {
    document.getElementById('auth-section').style.display = 'none';
    document.getElementById('dashboard-section').style.display = 'block';
    loadTasks();
}

// Tasks
async function loadTasks() {
    let url = API + '/tasks';
    if (currentFilter !== 'all') url += '?status=' + currentFilter;
    const res = await fetch(url, { headers: headers() });
    if (res.status === 401) { logout(); return; }
    const tasks = await res.json();
    const list = document.getElementById('task-list');
    if (tasks.length === 0) {
        list.innerHTML = '<p style="text-align:center;color:#999;padding:20px">No tasks</p>';
        return;
    }
    list.innerHTML = tasks.map(t => `
        <div class="task-item">
            <div class="task-info">
                <h4>${esc(t.title)}</h4>
                <p>${esc(t.description)}</p>
            </div>
            <span class="task-status ${t.status}">${t.status.replace('_',' ')}</span>
            <div class="task-actions">
                <button class="btn-edit" onclick="openEdit(${t.id},'${esc(t.title)}','${esc(t.description)}','${t.status}')">Edit</button>
                <button class="btn-delete" onclick="deleteTask(${t.id})">Delete</button>
            </div>
        </div>
    `).join('');
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function createTask() {
    const title = document.getElementById('task-title').value.trim();
    const description = document.getElementById('task-desc').value.trim();
    if (!title) return;
    await fetch(API + '/tasks', {
        method: 'POST', headers: headers(true),
        body: JSON.stringify({ title, description })
    });
    document.getElementById('task-title').value = '';
    document.getElementById('task-desc').value = '';
    loadTasks();
}

async function deleteTask(id) {
    await fetch(API + '/tasks/' + id, { method: 'DELETE', headers: headers() });
    loadTasks();
}

function openEdit(id, title, desc, status) {
    document.getElementById('edit-id').value = id;
    document.getElementById('edit-title').value = title;
    document.getElementById('edit-desc').value = desc;
    document.getElementById('edit-status').value = status;
    document.getElementById('edit-modal').style.display = 'flex';
}

function closeModal() { document.getElementById('edit-modal').style.display = 'none'; }

async function saveEdit() {
    const id = document.getElementById('edit-id').value;
    const title = document.getElementById('edit-title').value;
    const description = document.getElementById('edit-desc').value;
    const status = document.getElementById('edit-status').value;
    await fetch(API + '/tasks/' + id, {
        method: 'PUT', headers: headers(true),
        body: JSON.stringify({ title, description, status })
    });
    closeModal();
    loadTasks();
}

function filterTasks(f) {
    currentFilter = f;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    loadTasks();
}

// Init
if (getToken()) showDashboard();
