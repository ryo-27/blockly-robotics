import { cpSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
mkdirSync(root+'public/blockly',{recursive:true});
cpSync(root+'node_modules/blockly/media',root+'public/blockly/media',{recursive:true});
