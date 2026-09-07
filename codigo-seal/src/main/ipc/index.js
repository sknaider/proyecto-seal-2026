/**
 * Código SEAL — IPC Registry
 * Registers all IPC handlers from modular files.
 */

const terminal = require('./terminal.js');
const filesystem = require('./filesystem.js');
const git = require('./git.js');
const dialogs = require('./dialogs.js');
const browser = require('./browser.js');
const ai = require('./ai.js');
const lsp = require('../services/lspClient.js');
const bgAgents = require('../services/backgroundAgents.js');
const sessionMem = require('../services/sessionMemory.js');
const memExtractor = require('../services/memoryExtractor.js');
const coordinatorMode = require('../services/coordinatorMode.js');
const pluginSystem = require('../services/pluginSystem.js');
const soulAware = require('../services/soulAware.js');
const healthcareEval = require('../services/healthcareEval.js');
const riskScoring = require('../services/riskScoring.js');
const governanceCapture = require('../services/governanceCapture.js');
const dataSovereignty = require('../services/dataSovereignty.js');
const swarmPerms = require('../services/swarmPermissions.js');
const magicDocs = require('../services/magicDocs.js');
const autoDream = require('../services/autoDream.js');

function registerAll(mainWindow) {
  terminal.register(mainWindow);
  filesystem.register();
  git.register();
  dialogs.register(mainWindow);
  browser.register(mainWindow);
  ai.register(mainWindow);
  lsp.register(mainWindow);
  bgAgents.register(mainWindow);
  sessionMem.register();
  memExtractor.register(mainWindow);
  coordinatorMode.register(mainWindow);
  pluginSystem.register();
  soulAware.register(mainWindow);
  dataSovereignty.register();
  healthcareEval.register();
  riskScoring.register();
  governanceCapture.register();
  swarmPerms.register();
  magicDocs.register();
  autoDream.register();
}

function cleanup() {
  terminal.killAll();
  lsp.cleanup();
  bgAgents.cleanup();
}

module.exports = { registerAll, cleanup };
