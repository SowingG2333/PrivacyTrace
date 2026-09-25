# Geoapify MCP Server

This MCP server provides bounded geocoding and place-search access through
[Geoapify](https://www.geoapify.com/) and OpenStreetMap data. The historical
directory name is retained for compatibility with the frozen experiment
catalog.

## 配置

1. 在 [Geoapify MyProjects](https://myprojects.geoapify.com/) 创建项目并取得 API key。
2. 复制 `.env.example` 为 `.env`，至少配置：

```dotenv
MCP_SERVER_PORT=3001
GEOAPIFY_API_KEY=your_geoapify_api_key_here
GEOAPIFY_LANGUAGE=zh
```

3. 安装、构建并启动：

```bash
npm install
npm run build
npm start
```

也可以直接传参：

```bash
mcp-geoapify --port 3001 --apikey your_api_key_here
```

## MCP 工具

- `search_nearby`：附近地点与关键词搜索
- `get_place_details`：地点详情
- `maps_geocode`：地址转坐标
- `maps_reverse_geocode`：坐标转地址
- `maps_distance_matrix`：多起点/终点距离矩阵
- `maps_directions`：路线规划
- `maps_elevation`：海拔查询

工具名与原 Google Maps MCP 保持一致，因此大多数上层调用无需修改。地址型路线和矩阵输入会先通过 Geoapify Geocoding API 转成坐标。

## 数据差异

Geoapify/OSM 不提供 Google 式用户评分、评论数量、评论正文、价格等级或可靠的实时营业状态。为兼容旧输出，这些字段会返回 `null`、空数组或说明文本；`openNow` 和 `minRating` 入参仍可传入，但不会作为筛选条件。路线接口也不会根据 `departure_time` / `arrival_time` 重新选路，返回的出发和到达时间只是基于路线时长推算。

所有面向终端用户展示的结果都应保留返回值中的 Geoapify / OpenStreetMap attribution。接口及配额以 [Geoapify API 文档](https://apidocs.geoapify.com/) 和 [定价页](https://www.geoapify.com/pricing/) 为准。
