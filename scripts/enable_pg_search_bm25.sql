-- =============================================================================
-- 启用 pg_search（ParadeDB BM25）作为 RAG 关键词检索后端（可选，默认关闭）
--
-- 说明：
--   1. 本文件是"可选启用步骤"。默认系统仍用 PostgreSQL 内置全文检索（tsvector），
--      不需要本脚本也能运行。
--   2. 目标镜像（已实测，可同时保留 pgvector 与新增 pg_search）：
--        docker.m.daocloud.io/paradedb/paradedb:pg16
--        = PostgreSQL 16.15 + vector 0.8.4 + pg_search 0.25.6
--      也可用同 digest 的官方名：paradedb/paradedb:pg16（若服务器能直连 Docker Hub）。
--   3. 换镜像前必须：先备份数据卷，并确认卷挂载路径与 ParadeDB 镜像的 PGDATA 一致
--      （ParadeDB 的 PGDATA 常为 /var/lib/postgresql/<major>/docker，不同于 pgvector
--      镜像的 /var/lib/postgresql/data；若不一致，数据卷会"看起来丢了"）。
--   4. 执行前先让目标 Postgres 装上 pg_search 扩展（上述 ParadeDB 镜像已内置）。
--   5. 本脚本的 BM25 索引/分词写法需以你实际安装的 pg_search 版本为准（0.25.x）；
--      下方给出常用形态，请核对后取消注释再执行。
--
-- 执行：psql 到目标库后 source 本文件（幂等：重复执行不报错）。
-- =============================================================================

-- 1) 启用扩展（ParadeDB 镜像已内置；此处幂等声明）
CREATE EXTENSION IF NOT EXISTS pg_search;

-- 2) 在 knowledge_chunk.content 上建 BM25 索引。
--    提示：中文需配置支持中文的分词 tokenizer/analyzer；pg_search 自带中文分词能力
--    （不同版本 tokenizer 名/字段有差异，请以你的版本文档为准）。
--    pg_search BM25 索引语法随版本演进差异较大，二选一并取消注释、按你的版本核对：
--    旧版形态（key_field/text_field）：
--    CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_content_bm25
--        ON knowledge_chunk USING bm25 (content)
--        WITH (key_field = 'id', text_field = 'content');
--    新版形态（0.2x 常见，借助 config/schema 或缺省字段约定）：
--    CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_content_bm25
--        ON knowledge_chunk USING bm25 (content);

-- 注：上面 CREATE INDEX 因版本差异整体注释，避免版本不匹配时误执行。
--     请取消对应版本注释并按你的 pg_search 文档确认后再执行。

-- 3) 检索自检（可选；score()/查询语法以你的 pg_search 版本为准）：
--    SELECT id, paradedb.score(id)
--    FROM knowledge_chunk
--    WHERE content @@@ '稳健增值计划'
--    ORDER BY 2 DESC LIMIT 5;

