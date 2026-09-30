<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常；测绘控制卡片与台账、点位图共用同一聚合快照。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <h3 class="block-title">测绘控制看板（统一聚合 v{{ surveyStore.aggregate.version }}）</h3>
    <div class="stat-row">
      <article v-for="card in surveyStore.summaryCards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <div class="stat-row">
      <article v-for="item in surveyStore.typeRows" :key="item.name" class="stat-card">
        <span class="stat-label">{{ item.name }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>
    <p :class="surveyStore.consistencyOk ? 'ok-text' : 'bad-text'">
      分段总数 {{ surveyStore.aggregate.consistency.segment_total }} 与点位去重
      {{ surveyStore.aggregate.consistency.unique_points }}
      {{ surveyStore.consistencyOk ? '相符' : '不符' }}
    </p>

    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import { useSurveyAggregateStore } from '@/stores/surveyAggregate'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const surveyStore = useSurveyAggregateStore()

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = []
  }
  // 与侧栏、控制点清单、点位图工作台同一份快照
  await surveyStore.refresh({ force: true })
})
</script>
