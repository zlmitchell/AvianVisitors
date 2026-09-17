<?php
// Narrow JSON facade for the e-ink frame, shown under Tools when one is
// installed on this station. GET reports whether a frame is here and what its
// last forced refresh did; POST asks it to draw the panel now.
//
// The frame is optional and lives in its own checkout (frame/install.sh), so
// its absence is an answer rather than an error: without the control helper
// this returns installed:false and the page simply does not offer the card.
// Public requests require the configured admin gate, as every other action
// here does.

declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

require_once __DIR__ . '/admin-auth.php';
avian_require_admin();

function frame_response(int $status, array $body): void {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_SLASHES);
    exit;
}

$control = getenv('AV_FRAME_CONTROL') ?: '/usr/local/sbin/avian-frame-control';

function run_frame_control(string $control, string $action): array {
    $out = [];
    $rc = 0;
    exec('sudo -n ' . escapeshellarg($control) . ' ' . escapeshellarg($action) . ' 2>&1', $out, $rc);
    $decoded = json_decode(implode("\n", $out), true);
    if (!is_array($decoded)) {
        return ['status' => 500, 'body' => ['ok' => false, 'installed' => true,
                                            'error' => 'frame control returned an invalid response']];
    }
    return ['status' => $rc === 0 ? 200 : 409, 'body' => $decoded];
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if ($method === 'GET') {
    if (!is_executable($control)) {
        frame_response(200, ['ok' => true, 'installed' => false]);
    }
    $result = run_frame_control($control, 'status');
    frame_response($result['status'], $result['body']);
}
avian_require_json_action();
if (!is_executable($control)) {
    frame_response(503, ['ok' => false, 'installed' => false, 'error' => 'no frame is installed on this station']);
}
$body = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($body)) frame_response(400, ['ok' => false, 'installed' => true, 'error' => 'bad json']);
$action = (string)($body['action'] ?? '');
if ($action !== 'refresh') {
    frame_response(400, ['ok' => false, 'installed' => true, 'error' => 'unknown action']);
}
$result = run_frame_control($control, $action);
frame_response($result['status'], $result['body']);
