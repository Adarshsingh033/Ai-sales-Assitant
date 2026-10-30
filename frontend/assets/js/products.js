/**
 * Products & Catalog Controller
 * Manages product catalog, pricing, features, and inventory statuses.
 */

const ProductsModule = (() => {
  let products = [];
  let currentPage = 1;
  let pageSize = 10;
  let totalPages = 1;
  let totalCount = 0;
  let searchTimeout = null;
  let currentEditProductId = null;

  function formatCurrency(val, cur = 'USD') {
    if (!val && val !== 0) return '—';
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: cur || 'USD',
      }).format(val);
    } catch {
      return `$${val}`;
    }
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

  async function loadProducts() {
    const searchInput = document.getElementById('productSearchInput');
    const categoryFilter = document.getElementById('productCategoryFilter');

    const params = {
      page: currentPage,
      page_size: pageSize,
      search: searchInput?.value?.trim() || '',
      category: categoryFilter?.value || '',
    };

    const tbody = document.getElementById('productsTableBody');
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" class="db-empty">Loading products...</td></tr>`;
    }

    try {
      const res = await Api.getProducts(params);
      products = res.items || [];
      currentPage = res.page || 1;
      totalPages = res.pages || 1;
      totalCount = res.total || 0;
      renderTable();
      updatePaginationControls();
    } catch (err) {
      console.error('Failed to load products:', err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="db-empty" style="color:#e11d48;">Error loading products: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderTable() {
    const tbody = document.getElementById('productsTableBody');
    if (!tbody) return;

    if (products.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="db-empty">No products in catalog yet. Click "Add Product" to add your first product.</td></tr>`;
      return;
    }

    tbody.innerHTML = products.map(p => `
      <tr>
        <td>
          <div style="font-weight:600; color:#1e293b;">${escapeHtml(p.name)}</div>
          <div style="font-size:12px; color:#64748b; max-width:320px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
            ${escapeHtml(p.description || 'No description')}
          </div>
        </td>
        <td style="font-size:12.5px; color:#475569; font-family:monospace;">${escapeHtml(p.sku || '—')}</td>
        <td><span class="stage-pill stage-contacted">${escapeHtml(p.category || 'General')}</span></td>
        <td style="font-weight:700; color:#1e293b;">${formatCurrency(p.price, p.currency)}</td>
        <td>
          <span class="priority-pill ${p.is_active ? 'warm' : 'cold'}" style="${p.is_active ? 'background:#dcfce7; color:#15803d;' : 'background:#fee2e2; color:#b91c1c;'}">
            ${p.is_active ? 'Active' : 'Inactive'}
          </span>
        </td>
        <td>
          <div style="display:flex; gap:6px;">
            <button type="button" class="btn-secondary" style="padding:4px 8px; font-size:12px;" onclick="ProductsModule.openEditModal('${p.id}')">Edit</button>
            <button type="button" class="btn-secondary" style="padding:4px 8px; font-size:12px; color:#0369a1;" onclick="InventoryModule.openStockModal('${p.id}', '', 0, 0, 5, '${escapeHtml(p.name).replace(/'/g, "\\'")}')">Stock</button>
            <button type="button" class="btn-secondary" style="padding:4px 8px; font-size:12px; color:#dc2626;" onclick="ProductsModule.deleteProduct('${p.id}')">Delete</button>
          </div>
        </td>
      </tr>
    `).join('');
  }

  function updatePaginationControls() {
    const info = document.getElementById('productsPageIndicator');
    if (info) info.textContent = `Page ${currentPage} of ${totalPages || 1} (${totalCount} total)`;

    const btnFirst = document.getElementById('btnProductsFirst');
    const btnPrev = document.getElementById('btnProductsPrev');
    const btnCurrent = document.getElementById('btnProductsCurrent');
    const btnNext = document.getElementById('btnProductsNext');
    const btnLast = document.getElementById('btnProductsLast');

    if (btnFirst) btnFirst.disabled = currentPage <= 1;
    if (btnPrev) btnPrev.disabled = currentPage <= 1;
    if (btnCurrent) btnCurrent.textContent = currentPage;
    if (btnNext) btnNext.disabled = currentPage >= totalPages;
    if (btnLast) btnLast.disabled = currentPage >= totalPages;
  }

  function openCreateModal() {
    currentEditProductId = null;
    const title = document.getElementById('productModalTitle');
    if (title) title.textContent = 'Add New Product';

    const form = document.getElementById('productForm');
    if (form) form.reset();

    const cur = document.getElementById('productCurrency');
    if (cur) cur.value = 'USD';

    const active = document.getElementById('productIsActive');
    if (active) active.checked = true;

    const modal = document.getElementById('productModal');
    if (modal) modal.classList.add('active');
  }

  async function openEditModal(productId) {
    currentEditProductId = productId;
    const title = document.getElementById('productModalTitle');
    if (title) title.textContent = 'Edit Product';

    try {
      const p = await Api.getProduct(productId);
      document.getElementById('productName').value = p.name || '';
      document.getElementById('productSku').value = p.sku || '';
      document.getElementById('productCategory').value = p.category || '';
      document.getElementById('productPrice').value = p.price || 0;
      document.getElementById('productCurrency').value = p.currency || 'USD';
      document.getElementById('productDescription').value = p.description || '';
      document.getElementById('productFeatures').value = Array.isArray(p.features) ? p.features.join('\n') : (p.features || '');
      document.getElementById('productIsActive').checked = !!p.is_active;

      const modal = document.getElementById('productModal');
      if (modal) modal.classList.add('active');
    } catch (err) {
      if (window.showToast) window.showToast('Could not load product details: ' + err.message, 'error');
    }
  }

  function closeModal() {
    const modal = document.getElementById('productModal');
    if (modal) modal.classList.remove('active');
    currentEditProductId = null;
  }

  async function saveProduct(e) {
    if (e) e.preventDefault();

    const nameInput = document.getElementById('productName');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!name) {
      if (window.showToast) window.showToast('Product name is required.', 'error');
      return;
    }

    const priceInput = document.getElementById('productPrice');
    const priceVal = priceInput ? parseFloat(priceInput.value) : 0;
    const featuresText = document.getElementById('productFeatures')?.value || '';

    const payload = {
      name: name,
      sku: document.getElementById('productSku')?.value?.trim() || null,
      category: document.getElementById('productCategory')?.value?.trim() || 'General',
      price: isNaN(priceVal) ? 0 : priceVal,
      currency: document.getElementById('productCurrency')?.value?.trim() || 'USD',
      description: document.getElementById('productDescription')?.value?.trim() || null,
      features: featuresText.trim() || null,
      is_active: !!document.getElementById('productIsActive')?.checked,
    };

    const btnSave = document.getElementById('btnProductSave');
    const originalText = btnSave ? btnSave.textContent : 'Save Product';
    if (btnSave) {
      btnSave.disabled = true;
      btnSave.textContent = 'Saving...';
    }

    try {
      if (currentEditProductId) {
        await Api.updateProduct(currentEditProductId, payload);
        if (window.showToast) window.showToast('Product updated successfully', 'success');
      } else {
        await Api.createProduct(payload);
        if (window.showToast) window.showToast('Product created successfully', 'success');
      }
      closeModal();
      await loadProducts();
    } catch (err) {
      console.error('Failed to save product:', err);
      if (window.showToast) window.showToast('Failed to save product: ' + err.message, 'error');
    } finally {
      if (btnSave) {
        btnSave.disabled = false;
        btnSave.textContent = originalText;
      }
    }
  }

  async function deleteProduct(productId) {
    if (!confirm('Are you sure you want to delete this product?')) return;
    try {
      await Api.deleteProduct(productId);
      if (window.showToast) window.showToast('Product deleted', 'success');
      loadProducts();
    } catch (err) {
      if (window.showToast) window.showToast('Failed to delete product: ' + err.message, 'error');
    }
  }

  function setupEvents() {
    const searchInput = document.getElementById('productSearchInput');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
          currentPage = 1;
          loadProducts();
        }, 300);
      });
    }

    const categoryFilter = document.getElementById('productCategoryFilter');
    if (categoryFilter) {
      categoryFilter.addEventListener('change', () => {
        currentPage = 1;
        loadProducts();
      });
    }

    const form = document.getElementById('productForm');
    if (form) {
      form.addEventListener('submit', saveProduct);
    }

    // Pagination
    document.getElementById('btnProductsFirst')?.addEventListener('click', () => {
      if (currentPage > 1) { currentPage = 1; loadProducts(); }
    });
    document.getElementById('btnProductsPrev')?.addEventListener('click', () => {
      if (currentPage > 1) { currentPage--; loadProducts(); }
    });
    document.getElementById('btnProductsNext')?.addEventListener('click', () => {
      if (currentPage < totalPages) { currentPage++; loadProducts(); }
    });
    document.getElementById('btnProductsLast')?.addEventListener('click', () => {
      if (currentPage < totalPages) { currentPage = totalPages; loadProducts(); }
    });
  }

  return {
    init() {
      setupEvents();
      loadProducts();
    },
    loadProducts,
    openCreateModal,
    openEditModal,
    closeModal,
    deleteProduct
  };
})();

window.ProductsModule = ProductsModule;
