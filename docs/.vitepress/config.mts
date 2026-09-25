// VitePress 站点配置（由 docs-manage skill 维护侧边栏）
import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'TennisClip AI Docs',
  description: 'TennisClip AI 项目文档中心',
  lang: 'zh-CN',
  // 文档内存在指向仓库根文件（AGENTS.md / README.md / .codebuddy skill）及规划中
  // 目录（references/、guides/）的跨引用，这些并非 VitePress 页面，故忽略 dead link 检查。
  ignoreDeadLinks: true,
  // 参考代码目录（.gitignore 不入库）不参与文档站构建：内含外部项目 Markdown，
  // 其 HTML 片段会被当作 Vue 模板解析而中断构建（如 TennisDiary README 未闭合标签）。
  srcExclude: ['reference/**'],
  // 允许从任意主机（LAN / 容器 / 反代域名）访问 dev/preview 服务器。
  // VitePress 以 docs/ 为 Vite 根目录，不会自动读取仓库根的 vite.config.js，故在此显式声明。
  vite: {
    server: {
      host: true,
      allowedHosts: true,
    },
  },
  themeConfig: {
    nav: [
      { text: '方案', link: '/plans/01-需求分析与落地方案' },
      { text: '架构', link: '/architecture/' },
      { text: '参考', link: '/references/' },
      { text: '指南', link: '/guides/' },
    ],
    sidebar: {
      '/plans/': [
        {
          text: 'TennisClip AI 方案',
          items: [
            { text: '01：需求分析与落地方案', link: '/plans/01-需求分析与落地方案' },
            { text: '02：后端 loguru 日志 TDD 方案', link: '/plans/02-后端loguru日志TDD方案' },
            { text: '03：测试环境隔离方案', link: '/plans/03-测试环境隔离方案' },
            { text: '04：模型服务商管理', link: '/plans/04-模型服务商管理' },
            { text: '05：高光候选定位方案', link: '/plans/05-高光候选定位方案' },
            { text: '06：两步上传与 MD5 秒传', link: '/plans/06-两步上传与MD5秒传' },
            { text: '07：分析分层挂钩高光', link: '/plans/07-分析分层挂钩高光' },
            { text: '08：视频理解节点与策略模态框', link: '/plans/08-视频理解节点与策略模态框' },
            { text: '09：可编排工作流方案', link: '/plans/09-可编排工作流方案' },
            { text: '10：任务列表与历史记录界面', link: '/plans/10-任务列表与历史记录界面' },
            { text: '11：工作流模式整合主界面', link: '/plans/11-工作流模式整合主界面' },
            { text: '12：本地大模型部署可行性方案', link: '/plans/12-本地大模型部署可行性方案' },
            { text: '13：场景图关系感知节点方案', link: '/plans/13-场景图关系感知节点方案' },
          ],
        },
      ],
      '/architecture/': [
        { text: '架构总览', link: '/architecture/' },
      ],
      '/references/': [
        { text: 'API 参考', link: '/references/' },
      ],
      '/guides/': [
        {
          text: '使用指南',
          items: [
            { text: '指南索引', link: '/guides/' },
            { text: '01：CV 增强工作流使用说明', link: '/guides/01-CV增强工作流使用说明' },
          ],
        },
      ],
    },
  },
})
