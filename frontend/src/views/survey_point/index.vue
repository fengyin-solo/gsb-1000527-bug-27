<template>
  <section class="survey-layout" data-module="survey_point">
    <!-- 侧栏汇总视图：与看板/台账/点位图同一次聚合，核验后一起刷新，不再停留旧值 -->
    <aside class="survey-side">
      <h3>测绘控制汇总</h3>
      <p class="side-version">
        聚合版本 <strong>{{ control.dataVersion || '—' }}</strong>
        · 核验序列 <strong>{{ control.reviewSeq }}</strong>
      </p>
      <ul class="side-list">
        <li><span>控制点（去重）</span><strong>{{ stats?.控制点总数 ?? '—' }}</strong></li>
        <li><span>待复核 / 已复核</span><strong>{{ stats?.待复核 ?? 0 }} / {{ stats?.已复核 ?? 0 }}</strong></li>
        <li><span>跨图幅点</span><strong>{{ stats?.跨图幅点数 ?? 0 }}</strong></li>
        <li><span>迁移补数点</span><strong>{{ stats?.迁移补数点数 ?? 0 }}</strong></li>
        <li><span>责任组图幅</span><strong>{{ stats?.图幅数 ?? 0 }}</strong></li>
      </ul>
      <p class="side-check" :class="{ ok: control.segmentConsistent, bad: !control.segmentConsistent }">
        {{ control.segmentConsistent ? '分段总数与点位去重相符' : '分段总数与点位去重不符，已阻断发布' }}
      </p>
      <ul class="side-checks">
        <li v-for="check in control.aggregate?.checks ?? []" :key="check.name">
          <span :class="`dot ${check.status}`"></span>{{ check.name }}
        </li>
      </ul>
      <button class="btn primary block" type="button" :disabled="control.loading" @click="rebuild">
        {{ control.loading ? '重算中…' : '幂等批次重算聚合' }}
      </button>
      <p v-if="control.notice" class="side-notice">{{ control.notice }}</p>
      <p v-if="control.error" class="error-text">{{ control.error }}</p>
    </aside>

    <div class="survey-main">
      <header class="page-head">
        <div>
          <h2>测绘控制管理</h2>
          <p class="page-desc">看板统计、控制点台账与点位图工作台共用同一次聚合重算；点类型复核后三处同步回写。</p>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="exportRows">导出控制点台账</button>
        </div>
      </header>

      <!-- 看板统计 -->
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">控制点总数</span>
          <strong class="stat-value">{{ stats?.控制点总数 ?? 0 }}</strong>
          <small>原始记录 {{ stats?.原始记录数 ?? 0 }} 条，按点号去重</small>
        </article>
        <article class="stat-card">
          <span class="stat-label">待复核</span>
          <strong class="stat-value">{{ stats?.待复核 ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">已复核</span>
          <strong class="stat-value">{{ stats?.已复核 ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">跨图幅点</span>
          <strong class="stat-value">{{ stats?.跨图幅点数 ?? 0 }}</strong>
          <small>以最新签发坐标与责任组图幅为准</small>
        </article>
        <article class="stat-card">
          <span class="stat-label">迁移补数点</span>
          <strong class="stat-value">{{ stats?.迁移补数点数 ?? 0 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">点位图分段合计</span>
          <strong class="stat-value" :class="{ bad: !control.segmentConsistent }">{{ segmentTotal }}</strong>
          <small>须等于控制点总数 {{ stats?.控制点总数 ?? 0 }}</small>
        </article>
      </div>

      <div class="type-break">
        <span v-for="(count, type) in stats?.按点类型 ?? {}" :key="type" class="type-chip">
          {{ type }} <strong>{{ count }}</strong>
        </span>
      </div>

      <!-- 控制点台账 -->
      <section class="panel">
        <h3>控制点清单（台账）</h3>
        <form class="filter-bar" @submit.prevent="reloadLedger">
          <label class="filter-item">
            <span>点号</span>
            <input v-model="filters.keyword" placeholder="按点号检索" />
          </label>
          <label class="filter-item">
            <span>点类型</span>
            <select v-model="filters.point_type">
              <option value="">全部</option>
              <option v-for="type in pointTypes" :key="type" :value="type">{{ type }}</option>
            </select>
          </label>
          <label class="filter-item">
            <span>复核状态</span>
            <select v-model="filters.review">
              <option value="">全部</option>
              <option value="待复核">待复核</option>
              <option value="已复核">已复核</option>
            </select>
          </label>
          <label class="filter-item">
            <span>责任组图幅</span>
            <select v-model="filters.sheet">
              <option value="">全部</option>
              <option v-for="seg in control.segments" :key="seg.图幅编号" :value="seg.图幅编号">
                {{ seg.图幅名称 }}（{{ seg.图幅编号 }}）
              </option>
            </select>
          </label>
          <button class="btn" type="submit">查询</button>
          <button class="btn ghost" type="button" @click="resetFilters">重置</button>
        </form>

        <table class="data-table">
          <thead>
            <tr>
              <th>点号</th><th>点类型</th><th>最新坐标(X/Y)</th><th>签发版本</th>
              <th>责任组图幅</th><th>点位状态</th><th>复核状态</th><th>跨图幅</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in ledger" :key="String(row.id)">
              <td>{{ row.点号 }}</td>
              <td>
                <span v-if="editingId !== row.id">{{ row.点类型 }}</span>
                <select v-else v-model="editType">
                  <option v-for="type in pointTypes" :key="type" :value="type">{{ type }}</option>
                </select>
              </td>
              <td>{{ row.最新坐标.坐标X }} / {{ row.最新坐标.坐标Y }}</td>
              <td>v{{ row.签发版本 }}（共{{ row.coordinate_versions.length }}版）</td>
              <td>{{ row.图幅名称 }}<small>{{ row.责任组 }} · {{ row.图幅编号 }}</small></td>
              <td>{{ row.status }}</td>
              <td>
                <span class="review-tag" :class="row.复核状态 === '已复核' ? 'done' : 'pending'">{{ row.复核状态 }}</span>
                <small v-if="row.migrated">已补责任组</small>
              </td>
              <td>{{ row.跨图幅 ? '是' : '—' }}</td>
              <td class="row-actions">
                <template v-if="editingId !== row.id">
                  <button class="link" type="button" @click="startReview(row)">复核点类型</button>
                </template>
                <template v-else>
                  <button class="link" type="button" :disabled="submitting" @click="submitReview(row)">保存结论</button>
                  <button class="link" type="button" @click="cancelReview">取消</button>
                </template>
              </td>
            </tr>
            <tr v-if="!ledger.length">
              <td colspan="9" class="empty-state">暂无符合条件的控制点</td>
            </tr>
          </tbody>
        </table>
        <footer class="page-foot">
          <span>共 {{ ledgerTotal }} 条控制点（与看板同一聚合版本 {{ control.dataVersion }}）</span>
          <button class="btn ghost" type="button" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
          <button class="btn ghost" type="button" :disabled="page * pageSize >= ledgerTotal" @click="turnPage(1)">下一页</button>
          <span v-if="actionMessage" class="error-text">{{ actionMessage }}</span>
        </footer>
      </section>

      <!-- 点位图工作台 -->
      <section class="panel">
        <h3>点位图工作台（按责任组图幅）</h3>
        <div class="segment-grid">
          <article v-for="seg in control.segments" :key="seg.图幅编号" class="segment-card">
            <header>
              <strong>{{ seg.图幅名称 }}</strong>
              <span class="seg-count">{{ seg.point_count }} 点</span>
            </header>
            <p class="seg-meta">{{ seg.图幅编号 }} · {{ seg.责任组 }}</p>
            <ul class="pin-list">
              <li v-for="pin in seg.点位" :key="pin.点号">
                <span class="pin-name">
                  {{ pin.点号 }}
                  <em v-if="pin.跨图幅" class="cross-tag">跨图幅</em>
                </span>
                <span class="pin-type">{{ pin.点类型 }}</span>
                <span class="pin-coord">({{ pin.坐标X }}, {{ pin.坐标Y }})</span>
                <small>v{{ pin.历史版本数 }} 段历史</small>
              </li>
            </ul>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { useSurveyControlStore, type ControlPoint } from '@/stores/surveyControl'

const ENDPOINT = '/api/survey_point'
const control = useSurveyControlStore()
const pointTypes = ['三角点', 'GPS点', '导线点', '水准点', '图根点']

const stats = computed(() => control.stats)
const segmentTotal = computed(() =>
  control.segments.reduce((sum, item) => sum + item.point_count, 0),
)

const ledger = ref<ControlPoint[]>([])
const ledgerTotal = ref(0)
const page = ref(1)
const pageSize = 10
const filters = ref<{ keyword: string; point_type: string; review: string; sheet: string }>({
  keyword: '',
  point_type: '',
  review: '',
  sheet: '',
})
const editingId = ref<number | null>(null)
const editType = ref('')
const submitting = ref(false)
const actionMessage = ref('')

async function reloadLedger() {
  actionMessage.value = ''
  page.value = 1
  await loadPage()
}

async function loadPage() {
  try {
    const payload = await control.fetchLedger({ ...filters.value, page: page.value, size: pageSize })
    ledger.value = payload.items
    ledgerTotal.value = payload.total
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '控制点清单读取失败'
  }
}

function resetFilters() {
  filters.value = { keyword: '', point_type: '', review: '', sheet: '' }
  void reloadLedger()
}

async function turnPage(delta: number) {
  page.value += delta
  await loadPage()
}

function startReview(row: ControlPoint) {
  editingId.value = row.id
  editType.value = row.点类型
  actionMessage.value = ''
}

function cancelReview() {
  editingId.value = null
}

async function submitReview(row: ControlPoint) {
  submitting.value = true
  try {
    // 核验只发一个结论；store 用后端返回的重算快照一次性同步看板/侧栏/点位图，
    // 台账随后按新版本重取，保证三者 data_version 相同。
    const result = await control.reviewPoint(row.id, editType.value)
    actionMessage.value = result.message
    if (result.ok) {
      editingId.value = null
      await loadPage()
    }
  } finally {
    submitting.value = false
  }
}

async function rebuild() {
  const result = await control.rebuild(50)
  actionMessage.value = result.message
  if (result.ok) await loadPage()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(async () => {
  await control.loadAggregate()
  await loadPage()
})
</script>

<style scoped>
.survey-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 20px;
  align-items: start;
}
.survey-side {
  position: sticky;
  top: 16px;
  background: #fff;
  border: 1px solid #e3e6ee;
  border-radius: 10px;
  padding: 16px;
}
.survey-side h3 { margin: 0 0 8px; }
.side-version { color: #6b7280; font-size: 12px; margin: 0 0 12px; }
.side-list { list-style: none; margin: 0 0 12px; padding: 0; }
.side-list li {
  display: flex; justify-content: space-between; gap: 8px;
  padding: 6px 0; border-bottom: 1px dashed #eef0f5; font-size: 13px;
}
.side-check { font-size: 12px; padding: 6px 8px; border-radius: 6px; margin: 0 0 8px; }
.side-check.ok { background: #ecfdf3; color: #027a48; }
.side-check.bad { background: #fef3f2; color: #b42318; }
.side-checks { list-style: none; margin: 0 0 12px; padding: 0; font-size: 12px; color: #475467; }
.side-checks li { display: flex; align-items: center; gap: 6px; padding: 3px 0; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.通过 { background: #12b76a; }
.dot.失败 { background: #f04438; }
.dot.警告 { background: #f79009; }
.btn.block { width: 100%; }
.side-notice { font-size: 12px; color: #475467; margin: 8px 0 0; }
.survey-main { min-width: 0; }
.stat-card small { display: block; color: #98a2b3; font-size: 11px; margin-top: 4px; }
.stat-value.bad { color: #d92d20; }
.type-break { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 16px; }
.type-chip {
  background: #f2f4f7; border-radius: 999px; padding: 4px 12px; font-size: 12px; color: #344054;
}
.panel {
  background: #fff; border: 1px solid #e3e6ee; border-radius: 10px;
  padding: 16px; margin-bottom: 20px;
}
.panel h3 { margin: 0 0 12px; }
.data-table small { display: block; color: #98a2b3; }
.review-tag.done { color: #027a48; }
.review-tag.pending { color: #b54708; }
.segment-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px;
}
.segment-card {
  border: 1px solid #e3e6ee; border-radius: 8px; padding: 12px; background: #fcfcfd;
}
.segment-card header { display: flex; justify-content: space-between; align-items: center; }
.seg-count { background: #eff8ff; color: #175cd3; border-radius: 999px; padding: 2px 10px; font-size: 12px; }
.seg-meta { color: #667085; font-size: 12px; margin: 4px 0 8px; }
.pin-list { list-style: none; margin: 0; padding: 0; }
.pin-list li {
  display: grid; grid-template-columns: 1fr auto; gap: 2px 10px;
  padding: 6px 0; border-top: 1px dashed #eaecf0; font-size: 13px;
}
.pin-coord { grid-column: 1 / 3; color: #667085; font-size: 12px; }
.pin-list small { grid-column: 1 / 3; color: #98a2b3; }
.cross-tag {
  font-style: normal; background: #fffaeb; color: #b54708; border-radius: 4px;
  padding: 0 6px; font-size: 11px; margin-left: 6px;
}
</style>
