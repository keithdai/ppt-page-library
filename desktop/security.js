'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ALLOWED_IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp']);
const ALLOWED_ASSET_DIRECTORIES = new Set(['thumbnails', 'previews']);

function isMainSender(event, mainWindow) {
  if (!event || !mainWindow || mainWindow.isDestroyed()) return false;
  return event.sender === mainWindow.webContents &&
    event.senderFrame === mainWindow.webContents.mainFrame;
}

function requireMainSender(event, mainWindow) {
  if (!isMainSender(event, mainWindow)) throw new Error('不允许的请求');
}

function normalizeAssetPath(value) {
  const input = String(value || '');
  let decoded;
  try {
    decoded = decodeURIComponent(input);
  } catch {
    return null;
  }
  const withoutPrefix = decoded.startsWith('/assets/')
    ? decoded.slice('/assets/'.length)
    : decoded.replace(/^\/+/, '');
  if (!withoutPrefix || withoutPrefix.includes('\\') || withoutPrefix.includes('\0')) return null;
  const parts = withoutPrefix.split('/');
  if (parts.length !== 2 || !ALLOWED_ASSET_DIRECTORIES.has(parts[0])) return null;
  if (parts.some((part) => !part || part === '.' || part === '..')) return null;
  if (!/^[A-Za-z0-9_-]{1,160}\.(?:jpe?g|png|webp)$/i.test(parts[1])) return null;
  return parts.join('/');
}

function resolveAsset(assetsDir, requestedPath) {
  const relativePath = normalizeAssetPath(requestedPath);
  if (!relativePath) return null;
  let root;
  let target;
  try {
    root = fs.realpathSync(assetsDir);
    target = fs.realpathSync(path.join(root, relativePath));
  } catch {
    return null;
  }
  if (target !== root && !target.startsWith(`${root}${path.sep}`)) return null;
  if (!ALLOWED_IMAGE_EXTENSIONS.has(path.extname(target).toLowerCase())) return null;
  return target;
}

function assetUrl(assetsDir, requestedPath) {
  const relativePath = normalizeAssetPath(requestedPath);
  if (!relativePath) return '';
  const target = resolveAsset(assetsDir, relativePath);
  if (!target) return '';
  const version = fs.statSync(target).mtimeMs;
  return `pptlib-asset://local/${relativePath.split('/').map(encodeURIComponent).join('/')}?v=${version}`;
}

function createAssetProtocolHandler(getAssetsDir) {
  return function handleAssetRequest(request) {
    try {
      const url = new URL(request.url);
      if (url.hostname !== 'local' || url.username || url.password || url.port) {
        return new Response('not found', { status: 404 });
      }
      const target = resolveAsset(getAssetsDir(), url.pathname);
      if (!target) return new Response('not found', { status: 404 });
      const extension = path.extname(target).toLowerCase();
      const contentTypes = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
      };
      return new Response(fs.readFileSync(target), {
        headers: {
          'content-type': contentTypes[extension],
          'cache-control': 'no-store',
          'x-content-type-options': 'nosniff',
        },
      });
    } catch {
      return new Response('not found', { status: 404 });
    }
  };
}

module.exports = {
  assetUrl,
  createAssetProtocolHandler,
  isMainSender,
  normalizeAssetPath,
  requireMainSender,
  resolveAsset,
};
