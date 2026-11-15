/**
 * MeCoffee maintenance photo storage.
 * Deploy as Web app: Execute as Me; Who has access: Anyone.
 * Access is controlled by the random UPLOAD_SECRET in Script Properties.
 * Drive files remain private (no sharing permissions are created).
 */
const ROOT_FOLDER_ID = '1GDEUIAKb6odIM4cFD9aMasrGtZ8sfd5B';
const CLEANING_FOLDER_NAME = 'foto cisteni';

function doPost(e) {
  try {
    const data = JSON.parse((e.postData && e.postData.contents) || '{}');
    const secret = PropertiesService.getScriptProperties().getProperty('UPLOAD_SECRET');
    if (!secret || !data.secret || data.secret !== secret) {
      return reply_({ok: false, error: 'Unauthorized'});
    }
    const name = String(data.location || '').trim();
    if (!name || name.length > 120 || /[\\/]/.test(name)) {
      return reply_({ok: false, error: 'Invalid location'});
    }
    if (/zelezn|železn/i.test(name)) {
      return reply_({ok: false, error: 'Inactive location'});
    }
    const filename = String(data.filename || '');
    if (!/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[A-Za-z_]+\.(jpg|jpeg|png|webp|heic)$/.test(filename)) {
      return reply_({ok: false, error: 'Invalid filename'});
    }
    const bytes = Utilities.base64Decode(String(data.content_base64 || ''));
    if (!bytes.length || bytes.length > 15000000) {
      return reply_({ok: false, error: 'Invalid image size'});
    }
    const lock = LockService.getScriptLock();
    lock.waitLock(30000);
    let locationFolder;
    try {
      const root = DriveApp.getFolderById(ROOT_FOLDER_ID);
      const cleaning = getOrCreateFolder_(root, CLEANING_FOLDER_NAME);
      locationFolder = getOrCreateFolder_(cleaning, name);
    } finally {
      lock.releaseLock();
    }
    const mime = String(data.mime_type || 'image/jpeg');
    const blob = Utilities.newBlob(bytes, mime, filename);
    const file = locationFolder.createFile(blob);
    // Do not call setSharing: access remains inherited/private.
    return reply_({ok: true, file_id: file.getId(), url: file.getUrl()});
  } catch (err) {
    console.error('Photo upload failed: ' + err);
    return reply_({ok: false, error: String(err.message || err)});
  }
}

function getOrCreateFolder_(parent, name) {
  const existing = parent.getFoldersByName(name);
  return existing.hasNext() ? existing.next() : parent.createFolder(name);
}

function reply_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
