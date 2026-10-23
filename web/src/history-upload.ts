// Preserve the selected file's exact bytes before any upload. localStorage
// contains only its scoped locator and digest; IndexedDB stores the Blob.
function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('zhiyu-go-passive-history-v1', 1);
    req.onupgradeneeded = () => { req.result.createObjectStore('uploads'); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(new Error('无法保留历史原文件，尚未上传'));
  });
}
async function access(key: string, file?: Blob): Promise<Blob | undefined> {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('uploads', file ? 'readwrite' : 'readonly'), store = tx.objectStore('uploads');
      const req = file ? store.put(file, key) : store.get(key); let result: unknown;
      req.onsuccess = () => { result = req.result; };
      tx.oncomplete = () => resolve(file ?? (result instanceof Blob ? result : undefined));
      tx.onerror = tx.onabort = () => reject(new Error('历史原文件尚未完整保存，请保留原文件'));
    });
  } finally { db.close(); }
}
export async function saveHistoryUpload(key: string, file: Blob) { await access(key, file); const read = await access(key); if (!read || read.size !== file.size) throw new Error('历史原文件未完整回读，尚未上传'); }
export async function loadHistoryUpload(key: string) { const blob = await access(key); if (!blob) throw new Error('本机保留的历史原文件缺失，请保留原请求和原文件'); return blob; }
