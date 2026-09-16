// VitePress 站点配置（由 docs-manage skill 维护侧边栏）
import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'TennisClip AI Docs',
  description: 'TennisClip AI 项目文档中心',
  lang: 'zh-CN',
  // 文档内存在指向仓库根文件（AGENTS.md / README.md / .codebuddy skill）及规划中
  // 目录（references/、guides/）的跨引用，这些并非 VitePress 页面，故忽略 dead link 检查。
  ignoreDeadLinks: true,
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
        { text: '使用指南', link: '/guides/' },
      ],
    },
  },
})
