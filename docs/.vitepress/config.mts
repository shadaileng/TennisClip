// VitePress 站点配置（由 docs-manage skill 维护侧边栏）
import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'TennisClip AI Docs',
  description: 'TennisClip AI 项目文档中心',
  lang: 'zh-CN',
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
