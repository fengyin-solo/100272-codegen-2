<template>
  <section class="page" data-module="tug">
    <header class="page-head">
      <div>
        <h2>拖轮调度</h2>
        <p class="page-desc">
          按船型与拖带作业量、分水域口径核算所需马力；检修拖轮不参与派工，窗口撞车以先落库为准，
          马力不足说明差额并拦截，超出允许时段另行提示。所需马力一律由后端按统一口径计算。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reloadAll">刷新看板数据</button>
      </div>
    </header>

    <!-- 调度看板 -->
    <div class="stat-row">
      <article v-for="card in board.cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value" :class="{ warn: card.label === '超窗口提示' && card.value > 0 }">
          {{ card.value }}
        </strong>
      </article>
    </div>

    <table class="data-table area-table">
      <thead>
        <tr><th>水域</th><th>拖轮总数</th><th>待命条数</th><th>检修条数</th><th>待命可用马力</th><th>在执派工单</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in board.areas" :key="String(row['水域'])">
          <td>{{ row['水域'] }}</td>
          <td>{{ row['拖轮总数'] }}</td>
          <td>{{ row['待命条数'] }}</td>
          <td>{{ row['检修条数'] }}</td>
          <td>{{ row['可用马力'] }} 马力</td>
          <td>{{ row['在执派工单'] }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="board.warning_orders?.length" class="warn-box">
      <strong>超作业时段提示：</strong>
      <span v-for="(o, i) in board.warning_orders" :key="String(o.id)">
        {{ o['作业编号'] }}（{{ o['船名'] }}，{{ o['开始时间'] }}~{{ o['结束时间'] }}，允许 {{ o['允许时段'] }}）<span v-if="i < board.warning_orders.length - 1">；</span>
      </span>
    </div>

    <!-- 页签 -->
    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 派工登记 -->
    <form v-if="activeTab === 'dispatch'" class="dispatch-form" @submit.prevent="submitOrder">
      <div class="form-grid">
        <label class="form-item">
          <span>作业编号</span>
          <input v-model="form.jobNo" placeholder="如 JOB-2609-009（同编号再登记会覆盖旧版）" />
        </label>
        <label class="form-item">
          <span>船名</span>
          <input v-model="form.vesselName" placeholder="被协助船舶船名" />
        </label>
        <label class="form-item">
          <span>水域</span>
          <select v-model="form.area" @change="onAreaChange">
            <option value="" disabled>选择水域</option>
            <option v-for="area in areas" :key="area" :value="area">{{ area }}</option>
          </select>
        </label>
        <label class="form-item">
          <span>船型</span>
          <select v-model="form.vesselType">
            <option value="" disabled>选择船型</option>
            <option v-for="vt in vesselTypesForArea" :key="vt" :value="vt">{{ vt }}</option>
          </select>
        </label>
        <label class="form-item">
          <span>拖带作业量（{{ workloadUnit || '先选水域与船型' }}）</span>
          <input v-model.number="form.workload" type="number" min="0" step="any" placeholder="口径随船型而定" />
        </label>
        <label class="form-item">
          <span>开始时间</span>
          <input v-model="form.start" type="datetime-local" />
        </label>
        <label class="form-item">
          <span>结束时间</span>
          <input v-model="form.end" type="datetime-local" />
        </label>
        <label class="form-item form-item-wide">
          <span>指派拖轮（勾选；检修中不可选）</span>
          <span class="tug-pick">
            <label v-for="t in tugsForArea" :key="String(t.id)" class="tug-chip" :class="{ disabled: t.status === '检修中' || t.status === '停用' }">
              <input
                type="checkbox"
                :value="String(t['拖轮编号'])"
                :disabled="t.status === '检修中' || t.status === '停用'"
                v-model="form.tugs"
              />
              {{ t['拖轮编号'] }} · {{ t['额定马力'] }}马力 · {{ t.status }}
            </label>
            <span v-if="!tugsForArea.length" class="hint">请先选择水域</span>
          </span>
        </label>
      </div>

      <!-- 马力需求：后端 /calculate 实时返回，前端不自行计算 -->
      <div class="req-box" v-if="requirement">
        <div class="req-line">
          <span class="req-label">所需马力</span>
          <strong class="req-value">{{ requirement['所需马力'] }}</strong> 马力
          <span class="hint">（马力下限 {{ requirement['马力下限'] }}，建议 {{ requirement['建议条数'] }} 条 / 最少 {{ requirement['最少条数'] }} 条）</span>
        </div>
        <div class="req-line">
          <span class="req-label">已选合计</span>
          <strong :class="selectedHp >= requiredHp ? 'ok' : 'warn'">{{ selectedHp }}</strong> 马力
          <span v-if="selectedHp < requiredHp" class="warn">
            还差 {{ requiredHp - selectedHp }} 马力，提交会被后端拒绝
          </span>
          <span v-else class="ok">已达到所需马力</span>
        </div>
        <div class="req-line hint">{{ requirement['计算口径'] }}</div>
        <div class="req-line hint">该水域允许作业时段：{{ requirement['允许时段'] }}</div>
      </div>
      <div v-else class="req-box hint">选择水域、船型并填写作业量后，自动按后端口径试算所需马力。</div>

      <div class="form-actions">
        <button class="btn primary" type="submit">登记派工单</button>
        <span v-if="formMessage" :class="formOk ? 'ok-text' : 'error-text'">{{ formMessage }}</span>
        <span v-for="(w, i) in formWarnings" :key="i" class="warn-text">⚠ {{ w }}</span>
      </div>
    </form>

    <!-- 派工单 -->
    <template v-if="activeTab === 'orders'">
      <form class="filter-bar" @submit.prevent="reloadOrders">
        <label class="filter-item">
          <span>作业编号/船名</span>
          <input v-model="orderFilter.keyword" placeholder="按关键字检索" />
        </label>
        <label class="filter-item">
          <span>水域</span>
          <input v-model="orderFilter.area" placeholder="按水域过滤" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="col in orderColumns" :key="col">{{ col }}</th>
            <th>状态</th><th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in orders" :key="String(row.id)" :class="{ rowWarn: row['超窗口'] }">
            <td v-for="col in orderColumns" :key="col">{{ formatCell(row[col]) }}</td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <template v-if="row.status === '已派工'">
                <button class="link" type="button" @click="runOrderAction('完成作业', row)">完成作业</button>
                <button class="link" type="button" @click="runOrderAction('取消派工', row)">取消派工</button>
              </template>
              <span v-else class="hint">—</span>
            </td>
          </tr>
          <tr v-if="!orders.length">
            <td :colspan="orderColumns.length + 2" class="empty-state">暂无派工单</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ orderTotal }} 张派工单</span>
        <span v-if="orderMessage" class="error-text">{{ orderMessage }}</span>
      </footer>
    </template>

    <!-- 拖轮台账 -->
    <template v-if="activeTab === 'tugs'">
      <form class="filter-bar" @submit.prevent="reloadTugs">
        <label class="filter-item">
          <span>拖轮编号</span>
          <input v-model="tugFilter.keyword" placeholder="按编号检索" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <input v-model="tugFilter.status" placeholder="待命/执行中/检修中/停用" />
        </label>
        <button class="btn" type="submit">查询</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="col in tugColumns" :key="col">{{ col }}</th>
            <th>状态</th><th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tugs" :key="String(row.id)" :class="{ rowMaint: row.status === '检修中' }">
            <td v-for="col in tugColumns" :key="col">{{ row[col] ?? '—' }}</td>
            <td>{{ row.status }}</td>
            <td class="row-actions">
              <button v-if="row.status !== '检修中' && row.status !== '停用'" class="link" type="button" @click="runTugAction('登记检修', row)">登记检修</button>
              <button v-if="row.status === '检修中'" class="link" type="button" @click="runTugAction('修复归队', row)">修复归队</button>
            </td>
          </tr>
          <tr v-if="!tugs.length">
            <td :colspan="tugColumns.length + 2" class="empty-state">暂无拖轮台账数据</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot"><span>共 {{ tugTotal }} 条拖轮记录</span><span v-if="tugMessage" class="error-text">{{ tugMessage }}</span></footer>
    </template>

    <!-- 分水域派工口径 -->
    <template v-if="activeTab === 'standards'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="col in standardColumns" :key="col">{{ col }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in standards" :key="String(row.id)">            <td v-for="col in standardColumns" :key="col">{{ row[col] }}</td>
          </tr>
        </tbody>
      </table>
      <p class="hint">不同水域标准分开配置；派工、试算、看板读到的所需马力都按这张表的口径由后端计算。</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/tug'

type Row = Record<string, string | number | boolean | null>
type Requirement = Record<string, string | number> | null

const tabs = [
  { key: 'dispatch', label: '派工登记' },
  { key: 'orders', label: '派工单' },
  { key: 'tugs', label: '拖轮台账' },
  { key: 'standards', label: '分水域口径' },
]
const activeTab = ref('dispatch')

const orderColumns = [
  '作业编号', '船名', '船型', '水域', '作业量', '作业量单位',
  '开始时间', '结束时间', '拖轮', '所需马力', '合计马力', '建议条数', '实派条数',
  '允许时段', '超窗口', '提示', '登记时间',
]
const tugColumns = ['拖轮编号', '拖轮名称', '水域', '额定马力', '所属单位', '当前任务']
const standardColumns = ['水域', '船型', '作业量单位', '单位马力系数', '马力下限', '最少条数', '单条折算马力', '允许时段']

const board = ref<{
  cards: { label: string; value: number }[]
  areas: Row[]
  active_orders: Row[]
  warning_orders: Row[]
}>({ cards: [], areas: [], active_orders: [], warning_orders: [] })
const standards = ref<Row[]>([])
const tugs = ref<Row[]>([])
const orders = ref<Row[]>([])
const tugTotal = ref(0)
const orderTotal = ref(0)
const requirement = ref<Requirement>(null)

const tugFilter = reactive({ keyword: '', status: '' })
const orderFilter = reactive({ keyword: '', area: '' })

const form = reactive({
  jobNo: '',
  vesselName: '',
  area: '',
  vesselType: '',
  workload: undefined as number | undefined,
  start: '',
  end: '',
  tugs: [] as string[],
})

const formMessage = ref('')
const formOk = ref(false)
const formWarnings = ref<string[]>([])
const tugMessage = ref('')
const orderMessage = ref('')

const areas = computed(() => standards.value.map((s) => String(s['水域'])).filter((v, i, arr) => arr.indexOf(v) === i))
const vesselTypesForArea = computed(() =>
  standards.value.filter((s) => s['水域'] === form.area).map((s) => String(s['船型'])),
)
const workloadUnit = computed(() => {
  const hit = standards.value.find((s) => s['水域'] === form.area && s['船型'] === form.vesselType)
  return hit ? String(hit['作业量单位']) : ''
})
const tugsForArea = computed(() =>
  tugs.value
    .filter((t) => !form.area || t['水域'] === form.area)
    .sort((a, b) => String(a.status).localeCompare(String(b.status))),
)
const requiredHp = computed(() => Number(requirement.value?.['所需马力'] ?? 0))
// 已选拖轮的额定马力直接取自拖轮台账，仅用于即时提示；最终口径以后端返回为准。
const selectedHp = computed(() =>
  tugs.value
    .filter((t) => form.tugs.includes(String(t['拖轮编号'])))
    .reduce((sum, t) => sum + Number(t['额定马力'] || 0), 0),
)

function formatCell(value: unknown): string {
  if (value === true) return '是'
  if (value === false) return '否'
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function onAreaChange() {
  form.vesselType = ''
  form.tugs = []
}

// 试算加序号，避免慢响应覆盖新选择。
let calcSeq = 0
watch(
  () => [form.area, form.vesselType, form.workload],
  async () => {
    if (!form.area || !form.vesselType || !form.workload || form.workload <= 0) {
      requirement.value = null
      return
    }
    const seq = ++calcSeq
    const query = new URLSearchParams({
      area: form.area,
      vessel_type: form.vesselType,
      workload: String(form.workload),
    })
    try {
      const response = await request(`${ENDPOINT}/calculate?${query.toString()}`)
      if (!response.ok) {
        requirement.value = null
        return
      }
      const data = await response.json()
      if (seq === calcSeq) requirement.value = data
    } catch {
      requirement.value = null
    }
  },
)

async function reloadBoard() {
  const response = await request(`${ENDPOINT}/board`)
  if (response.ok) board.value = await response.json()
}

async function reloadStandards() {
  const response = await request(`${ENDPOINT}/standards`)
  if (response.ok) {
    const data = await response.json()
    standards.value = data.items ?? []
  }
}

async function reloadTugs() {
  tugMessage.value = ''
  const query = new URLSearchParams(
    Object.entries(tugFilter).filter(([, v]) => v) as [string, string][],
  ).toString()
  const response = await request(`${ENDPOINT}/tugs?${query}`)
  if (response.ok) {
    const data = await response.json()
    tugs.value = data.items ?? []
    tugTotal.value = data.total ?? tugs.value.length
  } else {
    tugMessage.value = '拖轮台账读取失败'
  }
}

async function reloadOrders() {
  const query = new URLSearchParams(
    Object.entries(orderFilter).filter(([, v]) => v) as [string, string][],
  ).toString()
  const response = await request(`${ENDPOINT}/orders?${query}`)
  if (response.ok) {
    const data = await response.json()
    orders.value = data.items ?? []
    orderTotal.value = data.total ?? orders.value.length
  }
}

async function reloadAll() {
  await Promise.all([reloadBoard(), reloadStandards(), reloadTugs(), reloadOrders()])
}

async function submitOrder() {
  formMessage.value = ''
  formWarnings.value = []
  formOk.value = false
  const payload = {
    values: {
      作业编号: form.jobNo,
      船名: form.vesselName,
      船型: form.vesselType,
      水域: form.area,
      作业量: form.workload === undefined ? '' : String(form.workload),
      开始时间: form.start,
      结束时间: form.end,
      拖轮: form.tugs.join(','),
    },
  }
  try {
    const response = await request(`${ENDPOINT}/orders`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      formMessage.value = data.message || data.detail || '派工未生效'
      return
    }
    formOk.value = true
    formWarnings.value = data.warnings ?? []
    formMessage.value = formWarnings.value.length
      ? `${data.message}（有超时段提示，请确认）`
      : data.message
    // 落库后一律以后端数据为准重新拉取，保证刷新、换班重进读到同一份。
    await Promise.all([reloadBoard(), reloadOrders(), reloadTugs()])
    form.jobNo = ''
    form.tugs = []
  } catch (error) {
    formMessage.value = error instanceof Error ? error.message : '派工请求失败'
  }
}

async function runOrderAction(action: string, row: Row) {
  orderMessage.value = ''
  const ok = await postAction(`${ENDPOINT}/orders/${row.id}/actions`, action)
  if (ok) {
    await Promise.all([reloadBoard(), reloadOrders(), reloadTugs()])
  } else {
    orderMessage.value = lastActionMessage.value
  }
}

async function runTugAction(action: string, row: Row) {
  tugMessage.value = ''
  const ok = await postAction(`${ENDPOINT}/tugs/${row.id}/actions`, action)
  if (ok) {
    await Promise.all([reloadBoard(), reloadTugs()])
  } else {
    tugMessage.value = lastActionMessage.value
  }
}

const lastActionMessage = ref('')

async function postAction(url: string, action: string): Promise<boolean> {
  try {
    const response = await request(url, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) {
      lastActionMessage.value = data.message || '操作未生效'
      return false
    }
    return true
  } catch (error) {
    lastActionMessage.value = error instanceof Error ? error.message : '操作失败'
    return false
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.warn { color: #b54708; }
.ok { color: #067647; }
.ok-text { color: #067647; }
.warn-text { color: #b54708; }
.hint { color: var(--muted); font-size: 12px; }
.area-table { margin-bottom: 12px; }
.warn-box {
  border: 1px solid #fedf89; background: #fffaeb; color: #b54708;
  border-radius: 8px; padding: 8px 12px; font-size: 13px; margin-bottom: 12px;
}
.tabs { display: flex; gap: 6px; margin: 12px 0; }
.tab { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 14px; cursor: pointer; }
.tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.dispatch-form { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.form-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.form-item { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.form-item span { color: var(--muted); font-size: 12px; }
.form-item input, .form-item select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.form-item-wide { grid-column: 1 / -1; }
.tug-pick { display: flex; flex-wrap: wrap; gap: 8px; }
.tug-chip {
  display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--border);
  border-radius: 999px; padding: 4px 10px; font-size: 12px; cursor: pointer;
}
.tug-chip.disabled { opacity: 0.5; cursor: not-allowed; }
.req-box { border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px; margin: 12px 0; }
.req-line { font-size: 13px; margin: 2px 0; }
.req-label { display: inline-block; min-width: 64px; color: var(--muted); }
.req-value { font-size: 18px; color: var(--brand); }
.form-actions { display: flex; align-items: center; gap: 12px; }
.rowWarn { background: #fffaeb; }
.rowMaint { background: #f2f4f7; }
</style>
