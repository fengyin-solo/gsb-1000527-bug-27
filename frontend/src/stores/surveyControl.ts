import { defineStore } from 'pinia'
import { request } from '@/api/client'

/** 测绘控制聚合快照：看板统计、控制点台账、点位图工作台共用同一份数据。 */
export type PointVersion = {
  版本: number
  坐标X: number | null
  坐标Y: number | null
  高程: number | null
  图幅编号?: string
  图幅名称?: string
  责任组?: string
  签发日期?: string
}

export type ControlPoint = Record<string, string | number | null | boolean | PointVersion[]> & {
  id: number
  点号: string
  点类型: string
  坐标X?: string
  坐标Y?: string
  图幅编号: string
  图幅名称: string
  责任组: string
  复核状态: '待复核' | '已复核'
  status: string
  跨图幅: boolean
  migrated?: boolean
  签发版本: number
  coordinate_versions: PointVersion[]
  最新坐标: { 版本: number; 坐标X: number | null; 坐标Y: number | null; 高程: number | null; 签发日期?: string }
}

export type Segment = {
  图幅编号: string
  图幅名称: string
  责任组: string
  point_count: number
  点号列表: string[]
  点位: { 点号: string; 点类型: string; 坐标X: number | null; 坐标Y: number | null; 跨图幅: boolean; 历史版本数: number }[]
}

export type AggregateStats = {
  控制点总数: number
  原始记录数: number
  待复核: number
  已复核: number
  跨图幅点数: number
  迁移补数点数: number
  图幅数: number
  按点类型: Record<string, number>
  按复核状态: Record<string, number>
  按点位状态: Record<string, number>
}

export type Aggregate = {
  data_version: number
  review_seq: number
  stats: AggregateStats
  points: ControlPoint[]
  segments: Segment[]
  checks: { name: string; status: string; detail: string }[]
  replayed?: boolean
  batch_ids?: string[]
}

export type LedgerQuery = {
  keyword?: string
  point_type?: string
  review?: string
  sheet?: string
  page?: number
  size?: number
}

type State = {
  aggregate: Aggregate | null
  loading: boolean
  error: string
  notice: string
}

export const useSurveyControlStore = defineStore('surveyControl', {
  state: (): State => ({
    aggregate: null,
    loading: false,
    error: '',
    notice: '',
  }),
  getters: {
    stats: (state) => state.aggregate?.stats ?? null,
    segments: (state) => state.aggregate?.segments ?? [],
    points: (state) => state.aggregate?.points ?? [],
    dataVersion: (state) => state.aggregate?.data_version ?? 0,
    reviewSeq: (state) => state.aggregate?.review_seq ?? 0,
    /** 分段总数必须与点位去重数相符；侧栏徽标直接读这条口径。 */
    segmentConsistent(): boolean {
      if (!this.aggregate) return false
      const total = this.segments.reduce((sum, item) => sum + item.point_count, 0)
      return total === this.aggregate.stats.控制点总数
    },
  },
  actions: {
    async loadAggregate(): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await request('/api/survey_point/aggregate')
        if (!response.ok) throw new Error('聚合快照读取失败')
        this.aggregate = await response.json()
      } catch (err) {
        this.error = err instanceof Error ? err.message : '测绘控制聚合读取失败'
      } finally {
        this.loading = false
      }
    },
    async fetchLedger(query: LedgerQuery): Promise<{ items: ControlPoint[]; total: number }> {
      const params = new URLSearchParams()
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== null && `${value}` !== '') params.set(key, `${value}`)
      })
      const response = await request(`/api/survey_point?${params.toString()}`)
      if (!response.ok) throw new Error('控制点清单读取失败')
      return response.json()
    },
    /**
     * 点类型复核：携带当前 review_seq 做乐观并发控制。
     * 成功后后端返回重算后的同一份聚合快照，一次写入即让看板/台账/点位图/侧栏同步。
     */
    async reviewPoint(entryId: number, pointType: string): Promise<{ ok: boolean; message: string }> {
      this.error = ''
      try {
        const response = await request(`/api/survey_point/${entryId}/review`, {
          method: 'POST',
          body: JSON.stringify({ values: { 点类型: pointType, expected_seq: this.reviewSeq } }),
        })
        const payload = await response.json()
        if (!payload.ok) {
          // 多半是被并发核验抢先：用后端随附的当前快照刷新本地，避免侧栏停留在旧版本
          if (payload.entry?.data_version !== undefined) {
            this.aggregate = payload.entry
          } else {
            await this.loadAggregate()
          }
          return { ok: false, message: payload.message }
        }
        this.aggregate = payload.entry
        this.notice = payload.message
        return { ok: true, message: payload.message }
      } catch (err) {
        return { ok: false, message: err instanceof Error ? err.message : '核验请求失败' }
      }
    },
    /** 幂等批次聚合重建；失败时后端保留旧快照，store 同样不覆盖本地成功统计。 */
    async rebuild(batchSize = 50): Promise<{ ok: boolean; message: string }> {
      this.loading = true
      try {
        const response = await request('/api/survey_point/aggregate/rebuild', {
          method: 'POST',
          body: JSON.stringify({ values: { batch_size: batchSize } }),
        })
        const payload = await response.json()
        if (!payload.ok) {
          // 未成功重算：保留当前 aggregate，不用空数据覆盖
          this.notice = `重算未生效，沿用上一版统计：${payload.message}`
          return { ok: false, message: payload.message }
        }
        this.aggregate = payload.entry
        this.notice = payload.message
        return { ok: true, message: payload.message }
      } catch (err) {
        const message = err instanceof Error ? err.message : '聚合重建失败'
        this.error = message
        return { ok: false, message }
      } finally {
        this.loading = false
      }
    },
  },
})
