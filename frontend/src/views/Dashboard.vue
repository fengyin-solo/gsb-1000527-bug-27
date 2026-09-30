<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标；测绘控制看板与台账、点位图同一次聚合，口径一致。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="loadAll">刷新看板</button>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <section v-if="surveyStats" class="survey-board">
      <h3>测绘控制看板（聚合版本 {{ surveyVersion }}）</h3>
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">控制点总数（去重）</span>
          <strong class="stat-value">{{ surveyStats.控制点总数 }}</strong>
          <small>原始记录 {{ surveyStats.原始记录数 }} 条</small>
        </article>
        <article class="stat-card">
          <span class="stat-label">待复核</span>
          <strong class="stat-value">{{ surveyStats.待复核 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">已复核</span>
          <strong class="stat-value">{{ surveyStats.已复核 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">跨图幅点</span>
          <strong class="stat-value">{{ surveyStats.跨图幅点数 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">迁移补数点</span>
          <strong class="stat-value">{{ surveyStats.迁移补数点数 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">点位图分段合计</span>
          <strong class="stat-value" :class="{ bad: !segmentConsistent }">{{ segmentTotal }}</strong>
          <small>与点位去重数{{ segmentConsistent ? '相符' : '不符' }}</small>
        </article>
      </div>
      <p class="board-link">
        <RouterLink to="/survey_point">前往控制点台账 / 点位图工作台执行点类型复核 →</RouterLink>
      </p>
    </section>

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
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import { useSurveyControlStore, type AggregateStats } from '@/stores/surveyControl'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

// 测绘控制卡片直接订阅聚合 store，与侧栏/台账/点位图同源
const control = useSurveyControlStore()
const surveyStats = computed<AggregateStats | null>(() => control.stats)
const surveyVersion = computed(() => control.dataVersion)
const segmentTotal = computed(() =>
  control.segments.reduce((sum, item) => sum + item.point_count, 0),
)
const segmentConsistent = computed(() => control.segmentConsistent)

async function loadAll() {
  const [overview] = await Promise.all([
    fetchJson<Overview>('/api/overview'),
    control.loadAggregate(),
  ])
  cards.value = overview.cards
  moduleRows.value = overview.modules
}

onMounted(loadAll)
</script>

<style scoped>
.survey-board {
  background: #fff;
  border: 1px solid #e3e6ee;
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 20px;
}
.survey-board h3 { margin: 0 0 12px; }
.stat-card small { display: block; color: #98a2b3; font-size: 11px; margin-top: 4px; }
.stat-value.bad { color: #d92d20; }
.board-link { margin: 8px 0 0; }
</style>
