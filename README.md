# ICT_Monitor

<p align="center">
  <strong>ICT 报文监控系统 · Internal Use Only</strong>
</p>

<p align="center">
  <sub>用于产线 ICT（In-Circuit Test）测试报文的实时监控与统计展示</sub>
</p>

---

## 📌 项目简介

**ICT_Monitor** 是一套面向内部产线的 ICT 测试报文监控系统，用于对测试数据进行 **实时监控、解析、统计与可视化展示**，以提升产线运行的稳定性与问题定位效率。

> 本仓库为 **私有仓库**，主要用于 **代码版本管理与发布记录**。

---

## ✨ 功能特性

- 📡 **实时监控**  
  监听产线目录中新生成的 ICT 测试报文并自动解析

- 📊 **看板统计**  
  按工装 / 板型 / 批次维度进行数据聚合展示

- 🕒 **历史查询**  
  支持按时间与条件查询历史测试数据

- 🗄️ **本地缓存**  
  基于 SQLite 的本地缓存机制，支持程序重启后数据恢复

- 🔒 **防误操作设计**  
  禁止通过右上角关闭按钮误关程序，降低产线风险

---

## 🚀 启动方式（开发环境）

```bash
conda activate deploy_ict
cd ICT_Monitor
python main.py
