# Self-contained on purpose: no Jinja, no static/style.css, no DB. Maintenance
# mode exists specifically for the case where none of those are guaranteed to
# be reachable, so the page has to work with literally nothing else running.
MAINTENANCE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cronbell — under maintenance</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #f2f6f6;
    color: #10171a;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 1.5rem;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #0c1315; color: #e7edee; }
    .card { background: #141d20 !important; border-color: #263134 !important; }
    .muted { color: #93a5aa !important; }
  }
  .card {
    max-width: 420px;
    width: 100%;
    background: #ffffff;
    border: 1px solid #d9e2e3;
    border-radius: 14px;
    padding: 2.25rem 2rem;
    text-align: center;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
    font-size: 1.05rem;
    margin-bottom: 1.5rem;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: #0e9594;
    flex-shrink: 0;
  }
  h1 { font-size: 1.3rem; margin: 0 0 0.75rem; }
  p { margin: 0 0 0.5rem; line-height: 1.55; }
  .muted { color: #57696e; font-size: 0.92rem; }
  a { color: #0e9594; }
</style>
</head>
<body>
  <div class="card">
    <span class="brand"><span class="dot"></span>Cronbell</span>
    <p class="muted">Formerly PulseCheck.</p>
    <h1>Under maintenance</h1>
    <p>The live demo is temporarily paused while I finish a UI rebuild and an infrastructure change.</p>
    <p class="muted">The source code, README, and full commit history are on <a href="https://github.com/shane-Coder/PulseCheck">GitHub</a> in the meantime.</p>
  </div>
</body>
</html>"""
