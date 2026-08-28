<?php
// AvianVisitors - bird image resolver.
//
// Lookup chain for /avian/api/cutout.php?sci=Calypte+anna:
//   1. ../assets/illustrations/<slug>.png   (333 bundled species, two poses each)
//   2. ../assets/cutouts/<slug>.png         (background-removed photo)
//   3. cached rembg of a Wikipedia photo at $HOME/BirdSongs/Extracted/cutouts/
//   4. fresh Wikipedia -> rembg -> cache (skipped gracefully if rembg unset)
//
// The frontend's <img src> points here for every species - bundled
// hits return instantly; cold misses fall through to the dynamic path.
//
// Bundled and cached images are public. A cold Wikipedia/rembg job is allowed
// only from the station's direct LAN address and only for a detected species.

declare(strict_types=1);

$sci = trim((string)($_GET['sci'] ?? ''));
if ($sci === '') {
    http_response_code(400);
    echo 'sci required';
    exit;
}
// Binomial / trinomial pattern. Rejects path-traversal payloads and
// junk before any filesystem or upstream lookup.
if (!preg_match('/^[A-Za-z]{2,40}(?:[ ][a-z]{2,40}){1,3}$/', $sci)) {
    http_response_code(400);
    echo 'invalid sci';
    exit;
}

// Slugify scientific name for filename + cache key.
$slug = preg_replace('/[^a-z0-9]+/', '-', strtolower($sci));
$slug = trim((string)$slug, '-');

// pose=1 (default) is perched. pose=2 is flight. Clamp to a two-digit
// positive integer so a malformed ?pose= can't break the path.
$pose = (int)($_GET['pose'] ?? 1);
if ($pose !== 2) $pose = 1;
$poseSuffix = $pose === 1 ? '' : "-$pose";

// Optional ?w= - the width the caller is actually going to draw this at.
//
// The illustrations are drawn for the website, where a postcard shows one at
// full size. The collage paints them into tiles: measured on a ten-bird plate,
// the source carried 12.8x more pixels than were drawn, and the worst single
// bird 67x - 741x1047 of PNG to fill 90x128. On a machine with room that is
// only wasted bandwidth. On the frame's Pi it is also a full-size RGBA bitmap
// resident in a browser that has 415MB to live in, which is the cost that does
// not show up in a byte count.
//
// Bucketed to 64px and capped, here as well as in the caller: a client is not
// trusted to keep the variant cache from exploding into one file per pixel.
$reqW = (int)($_GET['w'] ?? 0);
if ($reqW > 0) {
    $reqW = (int)(ceil(min($reqW, 1024) / 64) * 64);
}
// Variants live beside the rembg cache rather than in assets/, which is the
// repository's and gets replaced wholesale by an update.
$variantDir = dirname(__DIR__, 3) . '/BirdSongs/Extracted/cutouts/sized';

// Shrink $src to $w wide, preserving alpha, atomically. Returns false and
// leaves nothing behind if GD is missing or the encode fails, so a caller can
// always fall back to the original.
function make_variant(string $src, string $dst, int $w): bool {
    if (!function_exists('imagecreatefrompng')) return false;
    $im = @imagecreatefrompng($src);
    if ($im === false) return false;
    $sw = imagesx($im); $sh = imagesy($im);
    if ($sw < 1 || $sh < 1) { imagedestroy($im); return false; }
    $nw = $w;
    $nh = (int)max(1, round($sh * ($w / $sw)));
    $out = imagecreatetruecolor($nw, $nh);
    imagealphablending($out, false);
    imagesavealpha($out, true);
    imagecopyresampled($out, $im, 0, 0, 0, 0, $nw, $nh, $sw, $sh);
    imagedestroy($im);
    @mkdir(dirname($dst), 0755, true);
    $tmp = $dst . '.' . getmypid() . '.tmp';
    $ok = imagepng($out, $tmp, 6);
    imagedestroy($out);
    // rename is atomic on one filesystem, so a concurrent reader sees either
    // no variant or a complete one - never a half-written PNG.
    if ($ok && @rename($tmp, $dst)) return true;
    @unlink($tmp);
    return false;
}

function serve_png(string $path): void {
    global $reqW, $variantDir, $slug;
    // Only ever downscale. A tile larger than the illustration gets the
    // illustration; upscaling would spend cache on a worse picture.
    if ($reqW > 0 && $variantDir !== '') {
        $size = @getimagesize($path);
        if ($size !== false && (int)$size[0] > $reqW) {
            // Keyed on the resolved source, not on the request: pose=2 falls
            // back to the pose-1 file, and two sources must not share a key.
            $key = $slug . '-' . substr(sha1($path), 0, 8) . "-w$reqW.png";
            $variant = "$variantDir/$key";
            if (is_file($variant) || make_variant($path, $variant, $reqW)) {
                $path = $variant;
            }
        }
    }
    header('Content-Type: image/png');
    header('Cache-Control: public, max-age=86400');
    header('Content-Length: ' . (string)filesize($path));
    readfile($path);
    exit;
}

// 1. Bundled illustration with pose suffix (the kachō-e PNG the repo
//    ships with). The included set has 333 species in perched + flight poses.
$bundled = dirname(__DIR__) . "/assets/illustrations/{$slug}{$poseSuffix}.png";
if (is_file($bundled) && filesize($bundled) > 1024) {
    serve_png($bundled);
}
// Pose-2 missing? Fall back to pose-1 so the flight tab still shows
// the perched render instead of breaking to the photo fallback.
if ($pose !== 1) {
    $fallback = dirname(__DIR__) . "/assets/illustrations/$slug.png";
    if (is_file($fallback) && filesize($fallback) > 1024) {
        serve_png($fallback);
    }
}
// 2. Bundled cutout (background-removed photo, fallback for species
//    without an illustration).
$cutout = dirname(__DIR__) . "/assets/cutouts/$slug.png";
if (is_file($cutout) && filesize($cutout) > 1024) {
    serve_png($cutout);
}

// 3. Dynamic cache from a previous Wikipedia + rembg run.
$cacheDir = dirname(__DIR__, 3) . '/BirdSongs/Extracted/cutouts';
$cachePath = "$cacheDir/$slug.png";
if (is_file($cachePath) && filesize($cachePath) > 1024) {
    serve_png($cachePath);
}

// 4. Fresh Wikipedia fetch + rembg. Skipped if rembg-cli isn't on
//    PATH - the resolver returns a 404 in that case rather than
//    burning a Wikipedia request we can't use.
$rembg = '/usr/local/bin/rembg-cli';
if (!is_executable($rembg)) {
    http_response_code(404);
    echo 'no illustration bundled for ' . htmlspecialchars($sci) . ' (install rembg-cli to enable Wikipedia fallback)';
    exit;
}

require_once __DIR__ . '/admin-auth.php';
if (!avian_is_direct_local_request($_SERVER)) {
    http_response_code(404);
    echo 'no cached illustration for ' . htmlspecialchars($sci);
    exit;
}

$dbPath = dirname(__DIR__, 2) . '/scripts/birds.db';
if (!is_file($dbPath)) {
    http_response_code(404);
    echo 'species is not in this station';
    exit;
}
$db = new SQLite3($dbPath, SQLITE3_OPEN_READONLY);
$db->busyTimeout(1000);
$statement = $db->prepare('SELECT 1 FROM detections WHERE Sci_Name = :s LIMIT 1');
$statement->bindValue(':s', $sci, SQLITE3_TEXT);
$result = $statement->execute();
$detected = $result instanceof SQLite3Result && $result->fetchArray(SQLITE3_NUM) !== false;
$db->close();
if (!$detected) {
    http_response_code(404);
    echo 'species is not in this station';
    exit;
}

if (!is_dir($cacheDir)) @mkdir($cacheDir, 0755, true);
$lock = @fopen("$cacheDir/.cutout.lock", 'c');
if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) {
    if (is_resource($lock)) fclose($lock);
    http_response_code(429);
    header('Retry-After: 10');
    echo 'another cutout is being prepared';
    exit;
}

// Wikipedia's REST API asks for a contact-able identifier. Override
// via the AV_USER_AGENT env var (set in /etc/php/*/fpm/pool.d/www.conf
// or your shell) if your install hammers their endpoint at scale.
$ua = getenv('AV_USER_AGENT') ?: 'AvianVisitors/1.0 (+https://github.com/Twarner491/AvianVisitors)';
$ctx = stream_context_create([
    'http' => ['header' => "User-Agent: $ua\r\n", 'timeout' => 12],
]);
$wpUrl = 'https://en.wikipedia.org/api/rest_v1/page/summary/' . rawurlencode($sci);
$wpJson = @file_get_contents($wpUrl, false, $ctx);
$srcUrl = null;
if ($wpJson !== false) {
    $j = json_decode($wpJson, true);
    $srcUrl = $j['originalimage']['source'] ?? $j['thumbnail']['source'] ?? null;
}
// Defensive: only follow URLs on Wikimedia / Wikipedia hosts so a
// poisoned summary endpoint can't redirect us to arbitrary servers.
if ($srcUrl !== null) {
    $host = parse_url((string)$srcUrl, PHP_URL_HOST) ?: '';
    if (!preg_match('/(?:^|\.)(?:wikimedia\.org|wikipedia\.org)$/i', $host)) {
        $srcUrl = null;
    }
}
if (!$srcUrl) {
    http_response_code(404);
    echo 'no Wikipedia photo for ' . htmlspecialchars($sci);
    exit;
}

$imgBytes = @file_get_contents($srcUrl, false, $ctx, 0, 12 * 1024 * 1024);
if (!$imgBytes || strlen($imgBytes) < 1024) {
    http_response_code(503);
    echo 'failed to fetch source image';
    exit;
}

// rembg via the wrapper. u2netp = lightweight model (~50MB peak RAM -
// matters on the Pi 3B+). Temp files because rembg's CLI prefers
// real paths.
$tmpInBase  = tempnam(sys_get_temp_dir(), 'rembg-in-');
$tmpOutBase = tempnam(sys_get_temp_dir(), 'rembg-out-');
@unlink($tmpInBase); @unlink($tmpOutBase);
$tmpIn  = $tmpInBase  . '.jpg';
$tmpOut = $tmpOutBase . '.png';
file_put_contents($tmpIn, $imgBytes);

$cmd = sprintf(
    '%s i -m u2netp -ppm %s %s 2>&1',
    escapeshellarg($rembg),
    escapeshellarg($tmpIn),
    escapeshellarg($tmpOut)
);
$out = shell_exec($cmd);
@unlink($tmpIn);

if (!is_file($tmpOut) || filesize($tmpOut) < 1024) {
    @unlink($tmpOut);
    http_response_code(500);
    header('Content-Type: text/plain');
    echo "rembg failed (see your Pi's logs for details)";
    error_log("rembg failed for $sci: " . ($out ?? '(no output)'));
    exit;
}

// Tight-crop to the bird's bounding box + downscale to 800px max edge
// so cache stays small.
$im = @imagecreatefrompng($tmpOut);
if ($im !== false) {
    $cropped = @imagecropauto($im, IMG_CROP_TRANSPARENT);
    if ($cropped !== false) {
        imagedestroy($im);
        $im = $cropped;
    }
    $w = imagesx($im); $h = imagesy($im);
    $max = 800;
    if ($w > $max || $h > $max) {
        $scale = $max / max($w, $h);
        $nw = (int)($w * $scale); $nh = (int)($h * $scale);
        $resized = imagecreatetruecolor($nw, $nh);
        imagealphablending($resized, false);
        imagesavealpha($resized, true);
        imagecopyresampled($resized, $im, 0, 0, 0, 0, $nw, $nh, $w, $h);
        imagedestroy($im);
        $im = $resized;
    }
    imagealphablending($im, false);
    imagesavealpha($im, true);
    imagepng($im, $tmpOut, 6);
    imagedestroy($im);
}

// Atomic install: rename is atomic on the same filesystem, so any
// concurrent reader either sees the old cached file or the new one,
// never a half-written PNG.
@rename($tmpOut, $cachePath);
serve_png($cachePath);
