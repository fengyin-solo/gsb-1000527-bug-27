<template>
  <section class="page" data-module="survey_point_workbench">
    <header class="page-head">
      <div>
        <h2>点位图工作台</h2>
        <p class="page-desc">
          同一控制点跨图幅时，以最新签发坐标定位、以责任组图幅归属；历史坐标按签发版本保留。
          分段总数必须与点位去重数相符。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="rebuilding" @click="triggerRebuild">
          {{ rebuilding ? '重算中…' : '幂等批次重建' }}
        </button>
        <button class="btn" type="button" @click="refreshAll">重新加载</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="card in aggregate.summaryCards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <div class="stat-row">
      <article v-for="item in aggregate.typeRows" :key="item.name" class="stat-card">
        <span class="stat-label">{{ item.name }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="consistency-banner" :class="aggregate.consistencyOk ? 'ok-text' : 'bad-text'">
      分段总数 {{ aggregate.aggregate.consistency.segment_total }} /
      点位去重 {{ aggregate.aggregate.consistency.unique_points }} /
      原始签发落位 {{ aggregate.aggregate.consistency.raw_placement_total }}
      （跨图幅按责任组去重归属）——
      {{ aggregate.aggregate.consistency.matched ? '分段与去重相符，快照可用' : '分段与去重不符，快照已拒绝提交' }}
      · 快照 v{{ aggregate.aggregate.version }} · {{ aggregate.aggregate.rebuilt_at }}
    </p>

    <h3 class="block-title">分段图幅落位</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>图幅编号</th><th>图幅名称</th><th>责任组</th><th>权威点数（去重）</th><th>原始签发点数</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="segment in aggregate.aggregate.segments" :key="segment.图幅编号">
          <td>{{ segment.图幅编号 }}</td>
          <td>{{ segment.图幅名称 ?? '—' }}</td>
          <td>{{ segment.责任组 || '—' }}</td>
          <td>{{ segment.权威点数 }}</td>
          <td>{{ segment.原始签发点数 }}</td>
        </tr>
        <tr v-if="!aggregate.aggregate.segments.length">
          <td colspan="5" class="empty-state">暂无分段落位数据</td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">跨图幅控制点（最新签发坐标 + 责任组图幅归属）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>点号</th><th>点类型</th><th>责任组</th><th>权威图幅（责任组）</th>
          <th>台账图幅</th><th>最新签发图幅</th><th>最新版本</th>
          <th>签发时间</th><th>坐标X</th><th>坐标Y</th><th>高程</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="point in aggregate.aggregate.cross_sheet_points" :key="point.点号">
          <td>{{ point.点号 }}</td>
          <td>{{ point.点类型 }}</td>
          <td>{{ point.责任组 }}</td>
          <td>{{ point.权威图幅 }}</td>
          <td>{{ point.台账图幅 || '—' }}</td>
          <td>{{ point.最新签发图幅 || '—' }}</td>
          <td>v{{ point.最新版本 }}</td>
          <td>{{ point.签发时间 }}</td>
          <td>{{ point.坐标X ?? '—' }}</td>
          <td>{{ point.坐标Y ?? '—' }}</td>
          <td>{{ point.高程 ?? '—' }}</td>
        </tr>
        <tr v-if="!aggregate.aggregate.cross_sheet_points.length">
          <td colspan="11" class="empty-state">没有跨图幅控制点</td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">坐标签发版本（历史按版本保留）</h3>
    <table class="data-table">
      <thead>
        <tr><th>点号</th><th>最新版本</th><th>签发时间</th><th>签发图幅</th><th>历史版本数</th></tr>
      </thead>
      <tbody>
        <tr v-for="version in aggregate.aggregate.coord_versions" :key="version.点号">
          <td>{{ version.点号 }}</td>
          <td>v{{ version.version }}</td>
          <td>{{ version.签发时间 }}</td>
          <td>{{ version.图幅编号 || '—' }}</td>
          <td>{{ version.历史版本数 }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">存量迁移补数记录（缺责任组点）</h3>
    <table class="data-table">
      <thead>
        <tr><th>点号</th><th>补录图幅</th><th>补录责任组</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in aggregate.aggregate.migrated" :key="item.点号">
          <td>{{ item.点号 }}</td>
          <td>{{ item.图幅编号 ?? '—' }}</td>
          <td>{{ item.责任组 ?? '—' }}</td>
        </tr>
        <tr v-if="!aggregate.aggregate.migrated.length">
          <td colspan="3" class="empty-state">没有需要迁移补数的存量点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="aggregate.errorMessage" class="error-text">{{ aggregate.errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import { request } from '@/api/client'
import { useSurveyAggregateStore, type Aggregate } from '@/stores/surveyAggregate'

const aggregate = useSurveyAggregateStore()
const rebuilding = ref(false)
const errorMessage = ref('')

async function refreshAll() {
  errorMessage.value = ''
  await aggregate.refresh({ force: true })
}

async function triggerRebuild() {
  errorMessage.value = ''
  rebuilding.value = true
  try {
    const response = await request('/api/survey_point/rebuild', {
      method: 'POST',
      body: JSON.stringify({}),
    })
    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail ?? '聚合重建失败'
      throw new Error(detail)
    }
    const payload = await response.json()
    if (payload.aggregate) aggregate.applyAggregate(payload.aggregate as Aggregate)
  } catch (error) {
    // 重建失败时后端保留旧快照，前端同样不清空。
    errorMessage.value = error instanceof Error ? error.message : '聚合重建失败，旧统计保留'
  } finally {
    rebuilding.value = false
  }
}

refreshAll()
</script>
