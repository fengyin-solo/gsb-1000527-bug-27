import { defineStore } from 'pinia'

import { fetchJson } from '@/api/client'

/**
 * 测绘控制统一聚合：看板统计、控制点清单、点位图工作台与侧栏汇总
 * 都从这里取同一份快照。核验/重建成功后由动作发起方直接带回快照，
 * 其他页面通过 refresh 拉取，避免各处各算各的导致数量对不上。
 */

export type Segment = {
  图幅编号: string
  图幅名称: string | null
  责任组: string
  权威点数: number
  原始签发点数: number
}

export type Placement = {
  点号: string
  点类型: string
  权威图幅: string
  台账图幅: string
  图幅名称: string | null
  责任组: string
  最新签发图幅: string
  跨图幅: boolean
  最新版本: number
  签发时间: string
  坐标X: number | null
  坐标Y: number | null
  高程: number | null
}

export type CoordVersion = {
  点号: string
  version: number
  签发时间: string
  图幅编号: string
  历史版本数: number
}

export type MigratedPoint = { 点号: string; 图幅编号?: string; 责任组?: string }

export type AggregateStats = {
  控制点总数: number
  完好控制点: number
  损坏控制点: number
  已恢复控制点: number
  废弃控制点: number
  待复核点数: number
  跨图幅点数: number
  迁移补点数: number
  type_counts: Record<string, number>
  status_counts: Record<string, number>
}

export type Aggregate = {
  version: number
  rebuilt_at: string
  batch_id: string | null
  stats: AggregateStats
  segments: Segment[]
  cross_sheet_points: Placement[]
  coord_versions: CoordVersion[]
  migrated: MigratedPoint[]
  consistency: {
    segment_total: number
    unique_points: number
    raw_placement_total: number
    matched: boolean
  }
}

const EMPTY_STATS: AggregateStats = {
  控制点总数: 0,
  完好控制点: 0,
  损坏控制点: 0,
  已恢复控制点: 0,
  废弃控制点: 0,
  待复核点数: 0,
  跨图幅点数: 0,
  迁移补点数: 0,
  type_counts: {},
  status_counts: {},
}

const EMPTY_AGGREGATE: Aggregate = {
  version: 0,
  rebuilt_at: '',
  batch_id: null,
  stats: EMPTY_STATS,
  segments: [],
  cross_sheet_points: [],
  coord_versions: [],
  migrated: [],
  consistency: { segment_total: 0, unique_points: 0, raw_placement_total: 0, matched: true },
}

export const useSurveyAggregateStore = defineStore('surveyAggregate', {
  state: () => ({
    aggregate: { ...EMPTY_AGGREGATE, stats: { ...EMPTY_STATS, type_counts: {}, status_counts: {} } } as Aggregate,
    loaded: false,
    loading: false,
    errorMessage: '',
    lastSyncedAt: '',
  }),
  getters: {
    stats: (state) => state.aggregate.stats,
    summaryCards: (state) => {
      const stats = state.aggregate.stats
      return [
        { label: '控制点总数', value: stats.控制点总数 },
        { label: '待复核点数', value: stats.待复核点数 },
        { label: '跨图幅点数', value: stats.跨图幅点数 },
        { label: '迁移补点数', value: stats.迁移补点数 },
      ]
    },
    typeRows: (state) =>
      Object.entries(state.aggregate.stats.type_counts).map(([name, value]) => ({ name, value })),
    consistencyOk: (state) => state.aggregate.consistency.matched,
  },
  actions: {
    /** 核验/重建动作成功后直接用响应里的快照提交，保证三处视图同步到同一版本。 */
    applyAggregate(aggregate: Aggregate) {
      this.aggregate = aggregate
      this.loaded = true
      this.errorMessage = ''
      this.lastSyncedAt = new Date().toISOString()
    },
    async refresh({ force = false }: { force?: boolean } = {}) {
      if (this.loading) return
      // 已在本页面周期加载过则不重复拉；动作回写走 applyAggregate 保持一致。
      if (this.loaded && !force) return
      this.loading = true
      try {
        const aggregate = await fetchJson<Aggregate>('/api/survey_point/aggregate')
        this.applyAggregate(aggregate)
      } catch (error) {
        // 拉取失败时保留旧统计，绝不把已有数字清零。
        this.errorMessage = error instanceof Error ? error.message : '测绘控制汇总读取失败'
      } finally {
        this.loading = false
      }
    },
  },
})
