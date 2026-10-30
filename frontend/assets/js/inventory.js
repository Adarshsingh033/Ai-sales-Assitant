/**
 * Warehouse & Product Inventory Controller
 * Handles stock management across warehouse hubs, real-time availability, and low-stock alerts.
 */

const InventoryModule = (() => {
  let inventoryList = [];
  let warehouseList = [];

  async function loadData() {
    try {
      const [whs, invs] = await Promise.all([
        Api.getWarehouses(),
        Api.getInventory(),
      ]);
      warehouseList = whs || [];
      inventoryList = invs || [];

      renderWarehousePills();
      renderInventoryTable();
    } catch (err) {
      console.error('Failed to load inventory data:', err);
    }
  }

  function renderWarehousePills() {
    const container = document.getElementById('inventoryWarehouseList');
    if (!container) return;

    if (warehouseList.length === 0) {
      container.innerHTML = '<span style="color:#94a3b8;font-size:12px;">No warehouses configured</span>';
      return;
    }

    container.innerHTML = warehouseList.map(w => `
      <div style="background:#f8fafc;border:1px solid #e2e8f0;padding:6px 12px;border-radius:8px;display:flex;align-items:center;gap:8px;">
        <span style="font-weight:600;font-size:12px;color:#1e293b;">🏢 ${w.name}</span>
        <span style="font-size:11px;color:#64748b;">(${w.code}${w.city ? ' · ' + w.city : ''})</span>
      </div>
    `).join('');
  }

  function renderInventoryTable() {
    const tbody = document.getElementById('inventoryTableBody');
    if (!tbody) return;

    if (inventoryList.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="8" style="text-align:center;padding:32px;color:#94a3b8;">
            No warehouse stock records yet. Click "Adjust Stock" on any product to assign inventory.
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = inventoryList.map(item => {
      let badge = '<span class="stock-badge in-stock">● In Stock</span>';
      if (item.quantity_available === 0) {
        badge = '<span class="stock-badge out-of-stock">● Out of Stock</span>';
      } else if (item.is_low_stock) {
        badge = '<span class="stock-badge low-stock">⚠️ Low Stock</span>';
      }

      return `
        <tr>
          <td style="font-weight:600;color:#1e293b;">${escapeHtml(item.product_name)}</td>
          <td style="color:#64748b;font-family:monospace;">${item.product_sku || '—'}</td>
          <td>
            <div style="font-weight:500;">${escapeHtml(item.warehouse_name)}</div>
            <div style="font-size:11px;color:#94a3b8;">${item.warehouse_code}</div>
          </td>
          <td style="font-weight:600;">${item.quantity_on_hand}</td>
          <td style="color:#64748b;">${item.quantity_reserved}</td>
          <td style="font-weight:700;color:#1e293b;">${item.quantity_available}</td>
          <td>${badge}</td>
          <td style="text-align:right;">
            <button type="button" class="btn-secondary" style="padding:4px 10px;font-size:12px;" onclick="InventoryModule.openStockModal('${item.product_id}', '${item.warehouse_id}', ${item.quantity_on_hand}, ${item.quantity_reserved}, ${item.low_stock_threshold}, '${escapeHtml(item.product_name).replace(/'/g, "\\'")}')">Edit Stock</button>
          </td>
        </tr>
      `;
    }).join('');
  }

  async function openStockModal(productId, warehouseId, onHand = 0, reserved = 0, threshold = 5, productName = '') {
    const modal = document.getElementById('stockAdjustModal');
    if (!modal) return;

    // Set subtitle if product name provided or find product name
    const subtitleEl = document.getElementById('stockModalSubtitle');
    if (subtitleEl) {
      subtitleEl.textContent = productName ? `— ${productName}` : '';
    }

    // Ensure warehouses are loaded
    if (!warehouseList || warehouseList.length === 0) {
      try {
        const whs = await Api.getWarehouses();
        warehouseList = whs || [];
        renderWarehousePills();
      } catch (err) {
        console.error('Failed to load warehouses in openStockModal:', err);
      }
    }

    // If opened without warehouse info, look up existing inventory record for this product
    if (productId && (!warehouseId || onHand === 0)) {
      try {
        if (!inventoryList || inventoryList.length === 0) {
          const invs = await Api.getInventory();
          inventoryList = invs || [];
        }
        const existing = (inventoryList || []).find(item => item.product_id === productId);
        if (existing) {
          warehouseId = existing.warehouse_id;
          onHand = existing.quantity_on_hand;
          reserved = existing.quantity_reserved;
          threshold = existing.low_stock_threshold;
          if (subtitleEl && !productName && existing.product_name) {
            subtitleEl.textContent = `— ${existing.product_name}`;
          }
        }
      } catch (e) {
        console.warn('Could not prefill existing stock:', e);
      }
    }

    // Default to first warehouse if none selected
    if (!warehouseId && warehouseList.length > 0) {
      warehouseId = warehouseList[0].id;
    }

    document.getElementById('stockProdId').value = productId || '';
    document.getElementById('stockOnHand').value = onHand;
    document.getElementById('stockReserved').value = reserved;
    document.getElementById('stockThreshold').value = threshold;

    const whSelect = document.getElementById('stockWarehouseSelect');
    if (whSelect) {
      if (warehouseList.length === 0) {
        whSelect.innerHTML = '<option value="">No warehouses available</option>';
      } else {
        whSelect.innerHTML = warehouseList.map(w => `
          <option value="${w.id}" ${w.id === warehouseId ? 'selected' : ''}>${escapeHtml(w.name)} (${w.code}${w.city ? ' · ' + escapeHtml(w.city) : ''})</option>
        `).join('');
      }
    }

    modal.classList.add('active');
  }

  function closeStockModal() {
    const modal = document.getElementById('stockAdjustModal');
    if (modal) modal.classList.remove('active');
  }

  async function saveStock(e) {
    if (e) e.preventDefault();
    const productId = document.getElementById('stockProdId').value;
    const warehouseId = document.getElementById('stockWarehouseSelect').value;
    const onHand = parseInt(document.getElementById('stockOnHand').value, 10) || 0;
    const reserved = parseInt(document.getElementById('stockReserved').value, 10) || 0;
    const threshold = parseInt(document.getElementById('stockThreshold').value, 10) || 5;

    if (!productId) {
      if (window.showToast) window.showToast('Product ID is missing.', 'error');
      return;
    }
    if (!warehouseId) {
      if (window.showToast) window.showToast('Please select a warehouse hub.', 'error');
      return;
    }

    const submitBtn = document.querySelector('#stockForm button[type="submit"]') || document.querySelector('#stockAdjustModal button[type="submit"]');
    const origText = submitBtn ? submitBtn.textContent : 'Update Stock';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Updating...';
    }

    try {
      await Api.updateInventoryStock({
        product_id: productId,
        warehouse_id: warehouseId,
        quantity_on_hand: onHand,
        quantity_reserved: reserved,
        low_stock_threshold: threshold,
      });

      closeStockModal();
      if (window.showToast) window.showToast('Stock level updated successfully!', 'success');
      await loadData();
    } catch (err) {
      if (window.showToast) window.showToast('Failed to update stock: ' + err.message, 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = origText;
      }
    }
  }

  // Click outside backdrop to dismiss
  document.addEventListener('click', (e) => {
    const modal = document.getElementById('stockAdjustModal');
    if (modal && e.target === modal) {
      closeStockModal();
    }
  });

  // Escape key to dismiss
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const modal = document.getElementById('stockAdjustModal');
      if (modal && modal.classList.contains('active')) {
        closeStockModal();
      }
    }
  });

  function escapeHtml(text) {
    if (!text) return '';
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  return {
    loadData,
    openStockModal,
    closeStockModal,
    saveStock,
  };
})();
