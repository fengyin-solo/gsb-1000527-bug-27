<template>
  <section class="page" data-module="survey_point">
    <header class="page-head">
      <div>
        <h2>测绘控制管理</h2>
        <p class="page-desc">维护控制点台账，点类型复核与聚合重算同步回写看板、清单与点位图工作台。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记控制点</button>
        <button class="btn" type="button" :disabled="rebuilding" @click="triggerRebuild">
          {{ rebuilding ? '重算中…' : '幂等重建聚合' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出测绘控制清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="() => reload()">
      <label class="filter-item">
        <span>点号</span>
        <input v-model="keyword" placeholder="按点号检索" />
      </label>
      <label class="filter-item">
        <span>点位状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>点类型</span>
        <select v-model="typeFilter">
          <option value="">全部</option>
          <option v-for="pointType in pointTypes" :key="pointType" :value="pointType">{{ pointType }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>点类型复核</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <form class="inline-form" @submit.prevent="verifyRow(row)">
              <select v-model="verifyDraft[String(row.id)]">
                <option v-for="pointType in pointTypes" :key="pointType" :value="pointType">{{ pointType }}</option>
              </select>
              <button class="link" type="submit" :disabled="verifyingId === row.id">
                {{ row.pending_check ? '复核确认' : '重新复核' }}
              </button>
            </form>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无测绘控制数据，可先登记控制点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>
        共 {{ total }} 条控制点 · 台账去重总数 {{ aggregateStore.stats.控制点总数 }} ·
        快照 v{{ aggregateStore.aggregate.version }}
        <template v-if="aggregateStore.aggregate.consistency.matched"> · 分段与去重相符</template>
        <template v-else> · <span class="bad-text">分段与去重不符</span></template>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import {
  useSurveyAggregateStore,
  type Aggregate,
} from '@/stores/surveyAggregate'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/survey_point'
const columns = ['点号', '点类型', '坐标X', '坐标Y', '高程', '精度等级', '观测日期', '点位状态', '图幅编号', '责任组']
const actions = ['登记损坏', '安排恢复', '标记废弃']
const statuses = ['完好', '损坏', '已恢复', '废弃']
const pointTypes = ['GPS控制点', '三角点', '水准点', '图根点']

const aggregateStore = useSurveyAggregateStore()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const rebuilding = ref(false)
const verifyingId = ref<string | number | null>(null)
const keyword = ref('')
const statusFilter = ref('')
const typeFilter = ref('')
const verifyDraft = reactive<Record<string, string>>({})

const statCards = computed(() => {
  const stats = aggregateStore.stats
  return [
    { label: '控制点总数', value: stats.控制点总数 },
    { label: '完好控制点', value: stats.完好控制点 },
    { label: '损坏控制点', value: stats.损坏控制点 },
    { label: '待复核点数', value: stats.待复核点数 },
    { label: '跨图幅点数', value: stats.跨图幅点数 },
  ]
})

function applyAggregate(aggregate: Aggregate) {
  aggregateStore.applyAggregate(aggregate)
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  typeFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '控制点登记入口尚未接入审批流'
}

async function verifyRow(row: Row) {
  errorMessage.value = ''
  const draft = verifyDraft[String(row.id)] ?? String(row.点类型 ?? '')
  verifyingId.value = row.id as string | number
  try {
    const response = await request(`${ENDPOINT}/${row.id}/verify`, {
      method: 'POST',
      body: JSON.stringify({
        点类型: draft,
        expected_revision: Number(row.revision),
      }),
    })
    if (response.status === 409) {
      const detail = (await response.json().catch(() => ({}))).detail ?? '复核结论冲突'
      throw new Error(detail)
    }
    if (!response.ok) throw new Error('点类型复核未生效，请稍后重试')
    const payload = await response.json()
    // 核验结论与重算同一请求返回：同一快照同时回写看板统计、清单与工作台。
    if (payload.aggregate) applyAggregate(payload.aggregate as Aggregate)
    await reload({ preserveError: false })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '点类型复核失败'
  } finally {
    verifyingId.value = null
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('测绘控制动作未生效，请稍后重试')
    }
    const payload = await response.json()
    if (payload.ok === false) {
      errorMessage.value = payload.message
      return
    }
    if (payload.aggregate) applyAggregate(payload.aggregate as Aggregate)
    await reload({ preserveError: false })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '测绘控制操作失败'
  }
}

async function triggerRebuild() {
  errorMessage.value = ''
  rebuilding.value = true
  try {
    const response = await request(`${ENDPOINT}/rebuild`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail ?? '聚合重建失败'
      throw new Error(detail)
    }
    const payload = await response.json()
    if (payload.aggregate) applyAggregate(payload.aggregate as Aggregate)
    await reload({ preserveError: false })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '聚合重建失败，旧统计保留'
  } finally {
    rebuilding.value = false
  }
}

async function reload({ preserveError = true }: { preserveError?: boolean } = {}) {
  if (!preserveError) errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  if (typeFilter.value) query.set('point_type', typeFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('控制点列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    for (const row of rows.value) {
      if (verifyDraft[String(row.id)] === undefined) {
        verifyDraft[String(row.id)] = String(row.点类型 ?? pointTypes[0])
      }
    }
  } catch (error) {
    // 清单重载失败不清空统计侧数据
    errorMessage.value = error instanceof Error ? error.message : '测绘控制列表读取失败'
  }
}

onMounted(async () => {
  await aggregateStore.refresh({ force: true })
  await reload()
})
</script>
