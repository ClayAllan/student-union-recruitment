const XLSX = require("../dist/xlsx.full.min.js");

const headers = [
  "学号", "姓名", "学院", "第一志愿", "第二志愿", "第三志愿",
  "可面试时间", "联系电话", "备注",
];
const rows = [
  headers,
  ["240312", "林知夏", "经管学院", "权益部", "实践部", "办公室", "2026-10-09 18:30–20:00；2026-10-10 19:00–21:00", "13800000000", "周四需19点后"],
  ["240817", "周嘉言", "法学院", "活动部", "权益部", "宣媒部", "2026-10-09 19:00–19:30；2026-10-10 19:00–20:00", "", "示例数据，可删除"],
];

const worksheet = XLSX.utils.aoa_to_sheet(rows);
worksheet["!cols"] = [
  { wch: 12 }, { wch: 10 }, { wch: 16 }, { wch: 12 }, { wch: 12 },
  { wch: 12 }, { wch: 52 }, { wch: 16 }, { wch: 24 },
];
const guide = XLSX.utils.aoa_to_sheet([
  ["填表说明"],
  ["1. 第一行字段名称不要修改。"],
  ["2. 学号、姓名、学院、第一志愿为必填；第二、第三志愿可留空。"],
  ["3. 已填写的志愿不能重复，部门名称必须与系统一致。"],
  ["4. 可面试时间填写空闲区间，格式为 YYYY-MM-DD HH:MM–HH:MM，多个区间用中文分号分隔。"],
  ["5. 支持部门：权益部、办公室、活动部、实践部、宣媒部、公关部。"],
]);
const workbook = XLSX.utils.book_new();
XLSX.utils.book_append_sheet(workbook, worksheet, "报名数据");
XLSX.utils.book_append_sheet(workbook, guide, "填表说明");
const fs = require("node:fs");
const output = XLSX.write(workbook, { bookType: "xlsx", type: "buffer", compression: true });
fs.writeFileSync("dist/student-union-recruitment-template.xlsx", output);
