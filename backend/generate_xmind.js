#!/usr/bin/env node

const fs = require('fs');
const { Workbook, Topic, Zipper } = require('xmind');

function parseArgs() {
    const args = {};
    const argv = process.argv.slice(2);
    for (let i = 0; i < argv.length; i++) {
        if (argv[i] === '--input' && argv[i + 1]) {
            args.input = argv[i + 1];
            i++;
        } else if (argv[i] === '--output' && argv[i + 1]) {
            args.output = argv[i + 1];
            i++;
        } else {
            args.input = argv[i];
            args.output = argv[i + 1];
            i++;
        }
    }
    return args;
}

const { input, output } = parseArgs();

if (!input || !output) {
    console.error('Usage: node generate_xmind.js --input <json_path> --output <xmind_path>');
    process.exit(1);
}

try {
    const testcasesJson = fs.readFileSync(input, 'utf-8');
    const data = JSON.parse(testcasesJson);
    const outputPath = output;
    const { project_name, testcases } = data;
    
    // 创建Workbook
    const workbook = new Workbook();
    const sheet = workbook.createSheet('测试用例', project_name);
    const topic = new Topic({sheet});
    
    // 构建思维导图结构
    const rootId = topic.cid();
    
    for (let mi = 0; mi < testcases.length; mi++) {
        const module = testcases[mi];
        const modTitle = `模块：${module.name}`;
        topic.add({title: modTitle});
        topic.on(topic.cid(modTitle));
        
        for (let ti = 0; ti < module.testpoints.length; ti++) {
            const testpoint = module.testpoints[ti];
            const tpTitle = `测试点：${testpoint.name}`;
            topic.add({title: tpTitle});
            topic.on(topic.cid(tpTitle));
            
            for (const testcase of testpoint.cases) {
                topic.add({title: `用例：${testcase.title} #${testcase.priority}`});
                topic.on(topic.cid(`用例：${testcase.title} #${testcase.priority}`));
                
                if (testcase.preconditions && testcase.preconditions.trim()) {
                    topic.add({title: `前置条件：${testcase.preconditions}`});
                }
                
                if (testcase.steps && testcase.steps.length > 0) {
                    for (let i = 0; i < testcase.steps.length; i++) {
                        const stepText = testcase.steps[i].replace(/步骤\d+：/, '').trim();
                        topic.add({title: `步骤${i+1}：${stepText}`});
                    }
                }
                
                if (testcase.expected && testcase.expected.trim()) {
                    topic.add({title: `预期：${testcase.expected}`});
                }
                
                topic.on(topic.cid(tpTitle));
            }
            
            topic.on(topic.cid(modTitle));
        }
        
        topic.on(rootId);
    }
    
    // 解析文件路径
    const pathParts = outputPath.replace(/\\/g, '/').split('/');
    const fileName = pathParts.pop().replace('.xmind', '');
    const dirPath = pathParts.join('/');
    
    // 保存XMind文件
    const zipper = new Zipper({
        path: dirPath,
        workbook,
        filename: fileName
    });
    
    zipper.save().then(status => {
        if (status) {
            console.log('XMind file generated successfully');
            process.exit(0);
        } else {
            console.error('Failed to save XMind file');
            process.exit(1);
        }
    }).catch(err => {
        console.error('Error saving XMind file:', err.message);
        process.exit(1);
    });
    
} catch (error) {
    console.error('Error generating XMind:', error.message);
    console.error(error.stack);
    process.exit(1);
}