// 浏览器端文件 MD5 计算与分片读取助手（Web Crypto 不支持 MD5，故用 spark-md5）。
// 读取分片的同时增量累计 MD5，并为每片计算 crc32，避免二次读盘。

import SparkMD5 from 'spark-md5'

// 与后端 upload_service.CHUNK_SIZE_BYTES 保持一致的单一真源（5MB）
export const CHUNK_SIZE = 5 * 1024 * 1024

export function getFileExt(name) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

// 标准 CRC32（返回 8 位小写十六进制，与后端 zlib.crc32 对齐）
const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    }
    table[n] = c >>> 0
  }
  return table
})()

export function crc32(buf) {
  const view = new Uint8Array(buf)
  let crc = 0xffffffff
  for (let i = 0; i < view.length; i++) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ view[i]) & 0xff]
  }
  return ((crc ^ 0xffffffff) >>> 0).toString(16).padStart(8, '0')
}

// 一次性读取文件全部分片：增量计算 MD5 + 每片 crc32，返回可复用的分片描述。
// onProgress(0~1) 反映读取/哈希进度。
export async function readChunks(file, chunkSize = CHUNK_SIZE, onProgress) {
  const spark = new SparkMD5.ArrayBuffer()
  const size = file.size
  const total = Math.max(1, Math.ceil(size / chunkSize))
  const parts = []
  let read = 0
  for (let i = 0; i < total; i++) {
    const blob = file.slice(i * chunkSize, Math.min((i + 1) * chunkSize, size))
    const buf = await blob.arrayBuffer()
    spark.append(buf)
    parts.push({
      index: i,
      blob,
      length: buf.byteLength,
      crc32: crc32(buf),
    })
    read += buf.byteLength
    if (onProgress) onProgress(read / size)
  }
  return {
    md5: spark.end(),
    size,
    total,
    chunkSize,
    parts,
  }
}
