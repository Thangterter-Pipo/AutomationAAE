// Explicit user-triggered extension reload. No timers, automatic toggles or
// broad host permissions. Chrome may prompt on each management.setEnabled.
export const EXTENSION_ID = /^[a-p]{32}$/;

export async function managementReady(api = chrome) {
  if (!api.management || !api.permissions) throw new Error("Chrome không hỗ trợ quản lý extension trong phiên này.");
  if (await api.permissions.contains({ permissions: ["management"] })) return true;
  return api.permissions.request({ permissions: ["management"] });
}

export async function managedExtensions(api = chrome) {
  if (!await api.permissions.contains({ permissions: ["management"] }))
    throw new Error("Chưa cấp quyền quản lý extension.");
  const items = await api.management.getAll();
  return items.filter(item => item.type === "extension" && item.id !== api.runtime.id)
    .map(item => ({ id: item.id, name: item.name, enabled: item.enabled,
      mayDisable: item.mayDisable, mayEnable: item.mayEnable, version: item.version }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export async function reloadOtherExtension(extensionId, api = chrome) {
  if (!EXTENSION_ID.test(extensionId) || extensionId === api.runtime.id)
    throw new Error("ID extension đích không hợp lệ.");
  if (!await api.permissions.contains({ permissions: ["management"] }))
    throw new Error("Chưa cấp quyền quản lý extension.");
  const entry = await api.management.get(extensionId);
  if (entry.type !== "extension" || !entry.enabled || entry.mayDisable !== true)
    throw new Error("Extension phải đang bật và cho phép tắt/bật để reload.");
  // Toggling can disrupt in-flight jobs and may show a Chrome native prompt.
  await api.management.setEnabled(extensionId, false);
  let reenabled = false;
  try {
    await api.management.setEnabled(extensionId, true);
    reenabled = (await api.management.get(extensionId)).enabled === true;
    if (!reenabled) throw new Error("Chrome chưa xác nhận extension đã bật trở lại.");
  } catch (error) {
    // Try to restore, but never conceal a failure or claim success.
    try { await api.management.setEnabled(extensionId, true); }
    catch { /* user can restore with the dedicated Enable control */ }
    const current = await api.management.get(extensionId).catch(() => null);
    if (current?.enabled === true) {
      return { status: "restored", name: entry.name,
        message: "Chrome đã bật lại extension sau lỗi. Chưa xác minh reload hoàn tất." };
    }
    return { status: "needs-recovery", name: entry.name,
      message: "Chrome đã tắt extension nhưng chưa bật lại được. Dùng nút 'Bật lại' hoặc chrome://extensions." };
  }
  return { status: "reloaded", name: entry.name,
    message: "Đã tắt và bật lại extension. Cần mở lại popup/Side Panel hoặc tab nội bộ để kiểm chứng." };
}

export async function enableExtension(extensionId, api = chrome) {
  if (!EXTENSION_ID.test(extensionId) || extensionId === api.runtime.id)
    throw new Error("ID extension đích không hợp lệ.");
  if (!await api.permissions.contains({ permissions: ["management"] }))
    throw new Error("Chưa cấp quyền quản lý extension.");
  const entry = await api.management.get(extensionId);
  if (entry.type !== "extension" || entry.enabled) return { status: "already-enabled" };
  if (entry.mayEnable === false) throw new Error("Chrome không cho phép bật extension này.");
  await api.management.setEnabled(extensionId, true);
  if (!(await api.management.get(extensionId)).enabled)
    throw new Error("Chrome chưa xác nhận extension đã được bật.");
  return { status: "enabled", name: entry.name };
}
