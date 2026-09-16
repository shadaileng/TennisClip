// 根项目 Vite 配置（VitePress 文档站 dev/preview 经 docs/.vitepress/config.mts 的 vite 字段加载，
// 此根配置供在仓库根直接运行 Vite 的场景使用）。
import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    host: true,
    allowedHosts: true,
  },
})
