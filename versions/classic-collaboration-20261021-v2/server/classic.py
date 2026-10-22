import re
from errors import APIError
DEPARTMENTS = ['权益部', '办公室', '活动部', '实践部', '宣媒部', '公关部']
STATE_KEYS = {'candidates', 'schedules', 'records', 'rankings', 'matches', 'departmentAvailability', 'interviewConfig', 'quotas', 'rules', 'timeline', 'notificationTemplates'}

def validate_state(s):
    if not isinstance(s, dict) or set(s) != STATE_KEYS:
        raise APIError(400, '数据结构不正确，请刷新页面。')
    def safe(value, depth=0):
        if depth > 14:
            raise APIError(400, '数据嵌套过深。')
        if isinstance(value, str) and (len(value) > 20000 or '<' in value or '>' in value):
            raise APIError(400, '文本不能包含HTML标签或尖括号，且须在长度限制内。')
        if isinstance(value, dict):
            if any(k in ['__proto__', 'prototype', 'constructor'] for k in value):
                raise APIError(400, '数据含无效字段。')
            for v in value.values(): safe(v, depth + 1)
        elif isinstance(value, list):
            for v in value: safe(v, depth + 1)
    safe(s)
    for key in ['candidates', 'schedules', 'records', 'matches']:
        if not isinstance(s[key], list) or len(s[key]) > 20000:
            raise APIError(400, '记录数量或格式不正确。')
    ids = set()
    for c in s['candidates']:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', c['id']):
            raise APIError(400, '学号仅支持字母、数字、下划线和连字符。')
        prefs = c.get('prefs')
        if not isinstance(prefs, list) or not 1 <= len(prefs) <= 3 or len(set(prefs)) != len(prefs) or any(d not in DEPARTMENTS for d in prefs):
            raise APIError(400, '志愿需为1–3个不同的正式部门。')
        if c['id'] in ids or any(not isinstance(c.get(k), str) or not c[k].strip() for k in ['name', 'college']):
            raise APIError(400, '学号不得重复，姓名与学院不能为空。')
        ids.add(c['id'])
    candidates = {c['id']: c for c in s['candidates']}
    schedule_ids, record_keys = set(), set()
    for entry in s['schedules'] + s['records'] + s['matches']:
        if not isinstance(entry, dict) or entry.get('candidate') not in ids or entry.get('dept') not in DEPARTMENTS:
            raise APIError(400, '面试或录取记录引用了不存在的学生或部门。')
    for entry in s['schedules']:
        if entry['dept'] not in candidates[entry['candidate']]['prefs'] or not isinstance(entry.get('id'), int) or entry['id'] in schedule_ids:
            raise APIError(400, '面试编号重复或部门不在学生志愿中。')
        schedule_ids.add(entry['id'])
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(entry.get('date', ''))) or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(entry.get(k, ''))) for k in ['time', 'endTime']) or entry['endTime'] <= entry['time']:
            raise APIError(400, '面试时间须为同一天的有效起止时间。')
    for entry in s['records']:
        key = (entry['candidate'], entry['dept'])
        if key in record_keys or entry['dept'] not in candidates[entry['candidate']]['prefs'] or entry.get('decision') not in ['推荐', '候补观察', '不建议']:
            raise APIError(400, '面试评价重复或格式不正确。')
        record_keys.add(key)
        if not isinstance(entry.get('dims'), list) or len(entry['dims']) != 3 or any(v not in ['突出', '稳定', '待观察'] for v in entry['dims']):
            raise APIError(400, '面试评价需包含三项有效维度。')
    for key in ['rankings', 'departmentAvailability', 'interviewConfig', 'quotas']:
        if not isinstance(s[key], dict) or set(s[key]) != set(DEPARTMENTS):
            raise APIError(400, '部门配置不完整。')
    for dept in DEPARTMENTS:
        rank = s['rankings'][dept]
        if not isinstance(rank, list) or len(set(rank)) != len(rank) or any(i not in ids or dept not in candidates[i]['prefs'] for i in rank):
            raise APIError(400, '部门排序包含重复或无效学生。')
        quota = s['quotas'][dept]
        if not isinstance(quota, int) or not 0 <= quota <= 200:
            raise APIError(400, '部门名额须为0–200的整数。')
        cfg = s['interviewConfig'][dept]
        if not isinstance(cfg, dict) or cfg.get('mode') not in ['single', 'group']:
            raise APIError(400, '面试形式不正确。')
        for key, low, high in [('batchSize', 2, 50), ('singleDuration', 1, 240), ('groupDuration', 1, 240), ('gapMinutes', 0, 60)]:
            n = cfg.get(key)
            if not isinstance(n, int) or not low <= n <= high:
                raise APIError(400, '面试时长、间隔或人数不正确。')
        for slot in s['departmentAvailability'][dept]:
            if not isinstance(slot, dict) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(slot.get('date', ''))) or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(slot.get(k, ''))) for k in ['start', 'end']) or slot['end'] <= slot['start']:
                raise APIError(400, '部门时段格式不正确。')
    if not isinstance(s['rules'], dict) or any(not isinstance(v, bool) for v in s['rules'].values()):
        raise APIError(400, '招新规则格式不正确。')
    admitted = set()
    for match in s['matches']:
        if match['candidate'] in admitted or match.get('pref') not in [0, 1, 2, 3]:
            raise APIError(400, '同一学生不能重复录取。')
        admitted.add(match['candidate'])
    for dept in DEPARTMENTS:
        if sum(m['dept'] == dept for m in s['matches']) > s['quotas'][dept]:
            raise APIError(400, '录取人数超过部门名额。')
