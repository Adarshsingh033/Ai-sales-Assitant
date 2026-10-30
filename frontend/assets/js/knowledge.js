/**
 * Knowledge Base & RAG Controller
 * Handles document uploads (PDF, DOCX, TXT), chunking status, and semantic search testing.
 */

const KnowledgeModule = (() => {
  let documents = [];
  let selectedFile = null;

  function formatBytes(bytes, decimals = 1) {
    if (!bytes) return '0 B';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  function formatDate(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str.toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  async function loadDocuments() {
    const tbody = document.getElementById('knowledgeTableBody');
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" class="db-empty">Loading knowledge documents...</td></tr>`;
    }

    try {
      const res = await Api.getKnowledgeDocuments();
      documents = Array.isArray(res) ? res : (res?.items || []);
      renderDocumentsTable();
    } catch (err) {
      console.error('Failed to load knowledge documents:', err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="db-empty" style="color:#e11d48;">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderDocumentsTable() {
    const tbody = document.getElementById('knowledgeTableBody');
    if (!tbody) return;

    if (!Array.isArray(documents) || documents.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="db-empty">No documents uploaded yet. Upload sales PDFs, FAQs, or brochures above to teach your AI!</td></tr>`;
      return;
    }

    tbody.innerHTML = documents.map(d => `
      <tr>
        <td>
          <div style="font-weight:600; color:#1e293b;">${escapeHtml(d.filename || 'Document')}</div>
          <div style="font-size:12px; color:#64748b;">${escapeHtml(d.status || 'INDEXED')}</div>
        </td>
        <td><span class="priority-pill cold">${escapeHtml((d.file_type || 'TXT').toUpperCase())}</span></td>
        <td style="font-size:12.5px; color:#64748b;">${formatBytes(d.file_size)}</td>
        <td>
          <span class="stage-pill stage-qualified">${d.chunk_count || 0} chunks</span>
        </td>
        <td style="font-size:12px; color:#94a3b8;">${formatDate(d.created_at)}</td>
        <td>
          <button class="btn-secondary" style="padding:4px 8px; font-size:12px; color:#dc2626;" onclick="KnowledgeModule.deleteDocument('${d.id}')">
            Delete
          </button>
        </td>
      </tr>
    `).join('');
  }

  function handleFileSelection(file) {
    if (!file) return;
    const allowed = ['.pdf', '.docx', '.txt'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) {
      if (window.showToast) window.showToast('Please select a supported file (.pdf, .docx, .txt)', 'error');
      return;
    }

    selectedFile = file;
    const nameEl = document.getElementById('kbSelectedFileName');
    if (nameEl) nameEl.textContent = `Selected: ${file.name} (${formatBytes(file.size)})`;

    const titleInput = document.getElementById('kbDocTitle');
    if (titleInput && !titleInput.value) {
      titleInput.value = file.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ');
    }
  }

  async function uploadSelectedDocument() {
    if (!selectedFile) {
      if (window.showToast) window.showToast('Please select a file to upload', 'error');
      return;
    }

    const titleInput = document.getElementById('kbDocTitle');
    const title = titleInput?.value?.trim() || selectedFile.name;

    const btnUpload = document.getElementById('btnUploadKbDoc');
    const originalText = btnUpload?.textContent || 'Upload & Index';
    if (btnUpload) {
      btnUpload.disabled = true;
      btnUpload.textContent = 'Extracting & Indexing...';
    }

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('title', title);

    try {
      const res = await Api.uploadKnowledgeDocument(formData);
      const chunkCount = res.chunk_count ?? res.total_chunks ?? 0;
      if (window.showToast) window.showToast(`Document indexed successfully into ${chunkCount} chunks!`, 'success');

      selectedFile = null;
      if (titleInput) titleInput.value = '';
      const nameEl = document.getElementById('kbSelectedFileName');
      if (nameEl) nameEl.textContent = '';
      const fileInput = document.getElementById('kbFileInput');
      if (fileInput) fileInput.value = '';

      await loadDocuments();
    } catch (err) {
      if (window.showToast) window.showToast('Failed to upload document: ' + err.message, 'error');
    } finally {
      if (btnUpload) {
        btnUpload.disabled = false;
        btnUpload.textContent = originalText;
      }
    }
  }

  async function deleteDocument(docId) {
    if (!confirm('Are you sure you want to delete this document and its indexed knowledge?')) return;
    try {
      await Api.deleteKnowledgeDocument(docId);
      if (window.showToast) window.showToast('Document deleted', 'success');
      loadDocuments();
    } catch (err) {
      if (window.showToast) window.showToast('Failed to delete document: ' + err.message, 'error');
    }
  }

  async function testSearch() {
    const input = document.getElementById('kbTestSearchInput');
    const query = input?.value?.trim();
    if (!query) {
      if (window.showToast) window.showToast('Enter a search query to test knowledge retrieval', 'error');
      return;
    }

    const resultsContainer = document.getElementById('kbSearchResults');
    if (resultsContainer) {
      resultsContainer.innerHTML = '<div class="db-empty">Searching indexed vector chunks...</div>';
    }

    try {
      const res = await Api.searchKnowledge(query, 5);
      const matches = res.matches || res.results || [];

      if (!resultsContainer) return;
      if (matches.length === 0) {
        resultsContainer.innerHTML = '<div class="db-empty">No relevant chunks found above similarity threshold.</div>';
        return;
      }

      resultsContainer.innerHTML = matches.map((m, idx) => {
        const simScore = m.score ?? m.similarity_score ?? 0;
        const scorePct = Math.round(simScore * 100);
        return `
          <div class="kb-chunk-item">
            <div class="kb-chunk-header">
              <span>#${idx + 1} Chunk (${escapeHtml(m.filename || 'Knowledge Doc')})</span>
              <span class="kb-similarity-pill">${scorePct}% Match</span>
            </div>
            <div class="kb-chunk-text">${escapeHtml(m.content)}</div>
          </div>
        `;
      }).join('');
    } catch (err) {
      if (resultsContainer) {
        resultsContainer.innerHTML = `<div class="db-empty" style="color:#e11d48;">Search failed: ${escapeHtml(err.message)}</div>`;
      }
    }
  }

  function setupEvents() {
    const dropzone = document.getElementById('kbDropzone');
    const fileInput = document.getElementById('kbFileInput');

    if (dropzone && fileInput) {
      dropzone.addEventListener('click', () => fileInput.click());

      dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
      });

      dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('dragover');
      });

      dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
          handleFileSelection(e.dataTransfer.files[0]);
        }
      });

      fileInput.addEventListener('change', (e) => {
        if (e.target.files && e.target.files.length > 0) {
          handleFileSelection(e.target.files[0]);
        }
      });
    }

    const btnUpload = document.getElementById('btnUploadKbDoc');
    if (btnUpload) {
      btnUpload.addEventListener('click', uploadSelectedDocument);
    }

    const btnSearch = document.getElementById('btnKbTestSearch');
    if (btnSearch) {
      btnSearch.addEventListener('click', testSearch);
    }

    const testInput = document.getElementById('kbTestSearchInput');
    if (testInput) {
      testInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          testSearch();
        }
      });
    }
  }

  return {
    init() {
      setupEvents();
      loadDocuments();
    },
    loadDocuments,
    deleteDocument,
    testSearch
  };
})();

window.KnowledgeModule = KnowledgeModule;
