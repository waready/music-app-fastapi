#!/usr/bin/env node

/**
 * Version Update Script
 * Automatically updates version and build time in index.html before deployment
 */

const fs = require('fs');
const path = require('path');

function updateVersion() {
    const indexPath = path.join(__dirname, 'index.html');

    try {
        // Read current index.html
        let content = fs.readFileSync(indexPath, 'utf8');

        // Generate new version info
        const now = new Date();
        const buildTime = now.toISOString().slice(0, 16).replace('T', '-').replace(':', '-');

        // Get current version or increment
        const currentVersionMatch = content.match(/const APP_VERSION = '([^']+)'/);
        let newVersion = '1.0.0';

        if (currentVersionMatch) {
            const current = currentVersionMatch[1];
            const parts = current.split('.');
            const patch = parseInt(parts[2]) + 1;
            newVersion = `${parts[0]}.${parts[1]}.${patch}`;
        }

        console.log(`🔄 Updating to version ${newVersion} (${buildTime})`);

        // Update version in JavaScript
        content = content.replace(
            /const APP_VERSION = '[^']+'/,
            `const APP_VERSION = '${newVersion}'`
        );

        content = content.replace(
            /const BUILD_TIME = '[^']+'/,
            `const BUILD_TIME = '${buildTime}'`
        );

        // Update meta tags
        content = content.replace(
            /<meta name="version" content="[^"]*">/,
            `<meta name="version" content="${newVersion}">`
        );

        content = content.replace(
            /<meta name="build-time" content="[^"]*">/,
            `<meta name="build-time" content="${buildTime}">`
        );

        // Write updated content
        fs.writeFileSync(indexPath, content, 'utf8');

        console.log('✅ Version updated successfully!');
        console.log(`   Version: ${newVersion}`);
        console.log(`   Build Time: ${buildTime}`);

        // Create version info file for reference
        const versionInfo = {
            version: newVersion,
            buildTime: buildTime,
            timestamp: now.toISOString(),
            gitCommit: process.env.RENDER_GIT_COMMIT || 'unknown'
        };

        fs.writeFileSync(
            path.join(__dirname, 'version.json'),
            JSON.stringify(versionInfo, null, 2),
            'utf8'
        );

        console.log('📄 Created version.json file');

    } catch (error) {
        console.error('❌ Error updating version:', error);
        process.exit(1);
    }
}

// Run if called directly
if (require.main === module) {
    updateVersion();
}

module.exports = updateVersion;