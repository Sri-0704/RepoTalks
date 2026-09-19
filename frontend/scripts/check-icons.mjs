import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const appDir = path.join(rootDir, 'app');
const publicDir = path.join(rootDir, 'public');

const errors = [];

// Helper to check file existence
const exists = (p) => fs.existsSync(p);

// Helper to parse PNG dimensions (IHDR chunk at byte 16..24)
function getPngDimensions(filePath) {
  const buf = fs.readFileSync(filePath);
  const isPng = buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  if (!isPng) return null;
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  return { width, height };
}

// Helper to check ICO header (00 00 01 00)
function checkIcoHeader(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.length < 4) return { valid: false, isPng: false, header: buf.toString('hex') };
  const isPng = buf.subarray(0, 4).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  const isIco = buf[0] === 0 && buf[1] === 0 && buf[2] === 1 && buf[3] === 0;
  return { valid: isIco, isPng, header: buf.subarray(0, 4).toString('hex') };
}

// 1. Collision check: No public asset can share a name with App Router metadata icons
const metadataIconNames = ['favicon.ico', 'icon.png', 'apple-icon.png'];
if (exists(publicDir)) {
  const publicFiles = fs.readdirSync(publicDir);
  for (const name of metadataIconNames) {
    if (publicFiles.includes(name) && exists(path.join(appDir, name))) {
      errors.push(`Route collision detected: Both 'app/${name}' and 'public/${name}' exist, claiming '/${name}'.`);
    }
  }
  // Public directory must not contain redundant icon assets that claim metadata icon routes
  if (publicFiles.includes('icon.png')) {
    errors.push(`'public/icon.png' found. App Router file-based metadata in 'app/' must be the single owner of icons.`);
  }
  if (publicFiles.includes('favicon.ico')) {
    errors.push(`'public/favicon.ico' found. App Router file-based metadata in 'app/' must be the single owner of icons.`);
  }
}

// 2. Validate app/favicon.ico
const appFavicon = path.join(appDir, 'favicon.ico');
if (!exists(appFavicon)) {
  errors.push(`Missing required 'app/favicon.ico' file.`);
} else {
  const icoCheck = checkIcoHeader(appFavicon);
  if (icoCheck.isPng) {
    errors.push(`'app/favicon.ico' is a renamed PNG file. It must be a genuine ICO container starting with bytes 00 00 01 00.`);
  } else if (!icoCheck.valid) {
    errors.push(`'app/favicon.ico' has invalid header bytes (${icoCheck.header}). Expected 00 00 01 00.`);
  }
  const size = fs.statSync(appFavicon).size;
  const maxFaviconBytes = 50 * 1024; // 50 KB
  if (size > maxFaviconBytes) {
    errors.push(`'app/favicon.ico' exceeds size budget: ${(size / 1024).toFixed(1)} KB > 50 KB.`);
  }
}

// 3. Validate app/apple-icon.png
const appAppleIcon = path.join(appDir, 'apple-icon.png');
if (exists(appAppleIcon)) {
  const dims = getPngDimensions(appAppleIcon);
  if (!dims) {
    errors.push(`'app/apple-icon.png' is not a valid PNG file.`);
  } else {
    if (dims.width !== 180 || dims.height !== 180) {
      errors.push(`'app/apple-icon.png' must be exactly 180x180 pixels (got ${dims.width}x${dims.height}).`);
    }
    if (dims.width !== dims.height) {
      errors.push(`'app/apple-icon.png' must be square (got ${dims.width}x${dims.height}).`);
    }
  }
  const size = fs.statSync(appAppleIcon).size;
  const maxAppleBytes = 100 * 1024; // 100 KB
  if (size > maxAppleBytes) {
    errors.push(`'app/apple-icon.png' exceeds size budget: ${(size / 1024).toFixed(1)} KB > 100 KB.`);
  }
}

// 4. Validate any other app/icon.(png|jpg) if present
const appIconPng = path.join(appDir, 'icon.png');
if (exists(appIconPng)) {
  const dims = getPngDimensions(appIconPng);
  if (dims && dims.width !== dims.height) {
    errors.push(`'app/icon.png' must be square (got ${dims.width}x${dims.height}).`);
  }
}

if (errors.length > 0) {
  console.error('\n❌ [ICON CHECK FAILED] Icon validation errors found:');
  for (const err of errors) {
    console.error(`   • ${err}`);
  }
  console.error('\nReview AGENTS.md and icon requirements.\n');
  process.exit(1);
} else {
  console.log('✔ [ICON CHECK OK] All icon files verified (valid ICO/PNG signatures, square dimensions, within size budgets, no route collisions).');
  process.exit(0);
}
