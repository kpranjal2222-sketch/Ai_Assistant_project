const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const vendorDir = path.join(root, "web", "static", "vendor");
fs.mkdirSync(vendorDir, { recursive: true });

const assets = [
  ["chart.js/dist/chart.umd.js", "chart.umd.js"],
  ["lucide/dist/umd/lucide.min.js", "lucide.min.js"],
  ["chart.js/LICENSE.md", "chart.js-LICENSE.md"],
  ["lucide/LICENSE", "lucide-LICENSE"],
];

for (const [source, destination] of assets) {
  fs.copyFileSync(
    path.join(root, "node_modules", source),
    path.join(vendorDir, destination),
  );
}
