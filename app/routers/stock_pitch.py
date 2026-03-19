from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse
import pandas as pd
from typing import List, Dict, Any, Optional
from app.core.database import get_mongo_db
import logging
from datetime import datetime
import io
from pymongo import ASCENDING, DESCENDING

router = APIRouter(prefix="/stock_pitch", tags=["每日股票推荐，来自Trend–Emotion–Timing 策略"])
logger = logging.getLogger("stock_pitch")

# MongoDB集合名称
DEFAULT_COLLECTION = "stock_pitchs"

@router.post(
    "/create-unique-index",
    summary="创建唯一索引",
    description="在MongoDB中创建基于date和stock_code的唯一索引，防止重复插入"
)
async def create_unique_index(
    collection_name: str = Query(DEFAULT_COLLECTION, description="MongoDB集合名称")
):
    """
    创建唯一索引，确保同一天同一只股票只有一条记录
    """
    try:
        db = get_mongo_db()
        collection = db[collection_name]
        
        # 创建唯一索引
        index_name = await collection.create_index(
            [("date", ASCENDING), ("stock_code", ASCENDING)],
            unique=True,
            name="date_stock_unique"
        )
        
        return {
            "message": "唯一索引创建成功",
            "collection": collection_name,
            "index_name": index_name,
            "index_fields": ["date", "stock_code"]
        }
    except Exception as e:
        logger.error(f"创建索引失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"创建索引失败: {str(e)}")

@router.post(
    "/import-signals-with-deduplication",
    summary="导入信号数据（带去重）",
    description="上传Excel文件，自动去重，避免重复插入相同日期和股票代码的数据"
)
async def import_signals_with_deduplication(
    file: UploadFile = File(..., description="Excel文件（支持.xlsx, .xls格式）"),
    collection_name: str = Form(DEFAULT_COLLECTION, description="MongoDB集合名称"),
    limit: int = Form(10, description="返回记录数限制", ge=1, le=1000),
    deduplication_strategy: str = Form(
        "update", 
        description="去重策略: skip(跳过), update(更新), error(报错)",
        regex="^(skip|update|error)$"
    ),
    create_index_if_not_exists: bool = Form(True, description="如果索引不存在则自动创建")
):
    """
    导入信号数据，支持多种去重策略
    
    - **deduplication_strategy**: 
      - skip: 跳过重复数据
      - update: 更新重复数据
      - error: 遇到重复报错
    """
    try:
        # 验证文件格式
        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="文件格式错误，请上传Excel文件（.xlsx或.xls）")
        
        # 读取上传的文件内容
        contents = await file.read()
        
        # 使用pandas读取Excel
        try:
            df = pd.read_excel(io.BytesIO(contents))
            logger.info(f"成功读取Excel文件 {file.filename}，共 {len(df)} 条记录")
        except Exception as e:
            logger.error(f"读取Excel文件失败: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Excel文件读取失败: {str(e)}")
        
        # 检查必要字段
        required_fields = ['date', 'buy_signal', 'timing_indicator', 'stock_code']
        missing_fields = [field for field in required_fields if field not in df.columns]
        if missing_fields:
            raise HTTPException(
                status_code=400, 
                detail=f"Excel文件缺少必要字段: {', '.join(missing_fields)}"
            )
        
        # 获取MongoDB连接
        db = get_mongo_db()
        collection = db[collection_name]
        
        # 确保唯一索引存在
        if create_index_if_not_exists:
            try:
                # 检查索引是否存在
                indexes = await collection.index_information()
                if 'date_stock_unique' not in indexes:
                    await collection.create_index(
                        [("date", ASCENDING), ("stock_code", ASCENDING)],
                        unique=True,
                        name="date_stock_unique"
                    )
                    logger.info(f"为集合 {collection_name} 创建了唯一索引")
            except Exception as e:
                logger.warning(f"创建索引失败，可能已存在: {str(e)}")
        
        # 过滤buy_signal为1的数据
        filtered_df = df[df['buy_signal'] == 1]
        logger.info(f"过滤后得到 {len(filtered_df)} 条buy_signal=1的记录")
        
        if len(filtered_df) == 0:
            return JSONResponse(
                status_code=200,
                content={
                    "message": "没有找到buy_signal为1的记录",
                    "imported_count": 0,
                    "filename": file.filename
                }
            )
        
        # 按timing_indicator降序排序
        sorted_df = filtered_df.sort_values(by='timing_indicator', ascending=False)
        
        # 取前limit条
        top_n_df = sorted_df.head(limit)
        
        # 转换为字典列表并处理NaN值
        records = []
        for _, row in top_n_df.iterrows():
            record = {}
            for col in top_n_df.columns:
                value = row[col]
                # 处理NaN和特殊类型
                if pd.isna(value):
                    record[col] = None
                elif isinstance(value, (pd.Timestamp, datetime)):
                    record[col] = value.strftime('%Y-%m-%d')
                elif isinstance(value, (pd.Int64Dtype, pd.Float64Dtype)):
                    record[col] = float(value) if isinstance(value, float) else int(value)
                else:
                    record[col] = value
            records.append(record)
        
        # 添加导入时间戳
        import_time = datetime.now().isoformat()
        for record in records:
            record['import_time'] = import_time
            record['source_file'] = file.filename
            record['last_updated'] = import_time
        
        # 根据去重策略处理数据
        imported_count = 0
        skipped_count = 0
        updated_count = 0
        error_count = 0
        duplicates = []
        
        if deduplication_strategy == "skip":
            # 跳过重复数据
            for record in records:
                try:
                    # 检查是否存在
                    existing = await collection.find_one({
                        "date": record['date'],
                        "stock_code": record['stock_code']
                    })
                    
                    if existing:
                        logger.info(f"跳过重复数据: {record['date']} - {record['stock_code']}")
                        skipped_count += 1
                        duplicates.append({
                            "date": record['date'],
                            "stock_code": record['stock_code'],
                            "stock_name": record.get('stock_name')
                        })
                    else:
                        await collection.insert_one(record)
                        imported_count += 1
                except Exception as e:
                    logger.error(f"插入记录失败: {str(e)}")
                    error_count += 1
            
        elif deduplication_strategy == "update":
            # 更新重复数据
            for record in records:
                try:
                    result = await collection.update_one(
                        {
                            "date": record['date'],
                            "stock_code": record['stock_code']
                        },
                        {"$set": record},
                        upsert=True
                    )
                    
                    if result.upserted_id:
                        imported_count += 1
                    elif result.modified_count > 0:
                        updated_count += 1
                    else:
                        skipped_count += 1
                        
                except Exception as e:
                    logger.error(f"更新记录失败: {str(e)}")
                    error_count += 1
                    
        elif deduplication_strategy == "error":
            # 遇到重复报错
            try:
                result = await collection.insert_many(records, ordered=False)
                imported_count = len(result.inserted_ids)
            except Exception as e:
                # 检查是否是唯一索引冲突
                if "duplicate key error" in str(e):
                    # 找出重复的记录
                    for record in records:
                        existing = await collection.find_one({
                            "date": record['date'],
                            "stock_code": record['stock_code']
                        })
                        if existing:
                            duplicates.append({
                                "date": record['date'],
                                "stock_code": record['stock_code'],
                                "stock_name": record.get('stock_name')
                            })
                    
                    error_count = len(duplicates)
                    
                    return JSONResponse(
                        status_code=409,
                        content={
                            "message": "发现重复数据",
                            "error_detail": str(e),
                            "duplicates": duplicates,
                            "imported_count": 0,
                            "duplicate_count": error_count,
                            "suggestion": "请使用skip或update策略处理重复数据"
                        }
                    )
                else:
                    raise e
        
        # 获取日期分布统计
        date_counts = {}
        for record in records:
            date = record.get('date')
            if date:
                date_str = str(date)
                date_counts[date_str] = date_counts.get(date_str, 0) + 1
        
        return {
            "message": "数据导入成功",
            "imported_count": imported_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "collection": collection_name,
            "filename": file.filename,
            "deduplication_strategy": deduplication_strategy,
            "filter_criteria": "buy_signal = 1",
            "sort_by": "timing_indicator descending",
            "limit": limit,
            "date_distribution": date_counts,
            "duplicates_found": duplicates if duplicates else None
        }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入过程中发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")

@router.get(
    "/daily-pitch",
    summary="获取指定日期的股票推荐",
    description="根据日期查询当日的股票推荐（buy_signal=1），按timing_indicator降序排序"
)
async def get_daily_pitch(
    date: str = Query(..., description="查询日期 (格式: YYYY-MM-DD，例如: 2026-03-18)"),
    limit: int = Query(10, description="返回记录数限制", ge=1, le=100),
    include_stats: bool = Query(True, description="是否包含统计数据")
):
    """
    获取指定日期的股票推荐
    
    - **date**: 查询日期 (格式: YYYY-MM-DD)
    - **collection_name**: MongoDB集合名称
    - **limit**: 返回记录数限制（最多100条）
    - **include_stats**: 是否包含该日期的统计数据
    """
    try:
        # 验证日期格式
        try:
            query_date = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD 格式")
        
        # 获取MongoDB连接
        db = get_mongo_db()
        collection = db[DEFAULT_COLLECTION]
        
        # 构建查询条件 - 只查询buy_signal为1的数据
        query = {
            "$and": [
                {
                    "$or": [
                        {"date": date},
                        {"date": query_date},
                        {"date": query_date.strftime('%Y-%m-%d')}
                    ]
                },
                {"buy_signal": 1}
            ]
        }
        
        # 查询总数
        total_count = await collection.count_documents(query)
        
        if total_count == 0:
            return {
                "date": date,
                "total_count": 0,
                "returned_count": 0,
                "message": f"没有找到 {date} 的股票推荐数据",
                "recommendations": []
            }
        
        # 查询记录，按timing_indicator降序排序
        cursor = collection.find(query).sort("timing_indicator", DESCENDING).limit(limit)
        
        # 转换ObjectId为字符串并格式化返回数据
        recommendations = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            
            # 只返回需要的字段，并按重要性排序
            recommendation = {
                "rank": len(recommendations) + 1,
                "stock_code": doc.get("stock_code"),
                "stock_name": doc.get("stock_name"),
                "industry": doc.get("industry"),
                "timing_indicator": doc.get("timing_indicator"),
                "trend_score": doc.get("trend_score"),
                "anchored_trend_score": doc.get("anchored_trend_score"),
                "emotion_index": doc.get("emotion_index"),
                "combined_trend_score": doc.get("combined_trend_score"),
                "bench_trend_score": doc.get("bench_trend_score"),
                "date": doc.get("date")
            }
            recommendations.append(recommendation)
        
        # 获取该日期的统计数据
        stats = None
        if include_stats and total_count > 0:
            stats = await get_date_stats(collection, date)
        
        return {
            "date": date,
            "total_count": total_count,
            "returned_count": len(recommendations),
            "limit": limit,
            "stats": stats,
            "recommendations": recommendations
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询每日推荐过程中发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

@router.get(
    "/latest-pitch",
    summary="获取最新日期的股票推荐",
    description="查询数据库中最新日期的股票推荐，按timing_indicator降序排序"
)
async def get_latest_pitch(
    limit: int = Query(10, description="返回记录数限制", ge=1, le=100),
    include_stats: bool = Query(True, description="是否包含统计数据")
):
    """
    获取最新日期的股票推荐
    
    - **limit**: 返回记录数限制（最多100条）
    - **include_stats**: 是否包含该日期的统计数据
    """
    try:
        # 获取MongoDB连接
        db = get_mongo_db()
        collection = db[DEFAULT_COLLECTION]
        
        # 获取最新的日期
        latest_date_pipeline = [
            {"$match": {"buy_signal": 1}},
            {"$group": {"_id": "$date"}},
            {"$sort": {"_id": -1}},
            {"$limit": 1}
        ]
        
        cursor = collection.aggregate(latest_date_pipeline)
        latest_dates = await cursor.to_list(length=1)
        
        if not latest_dates:
            return {
                "message": "数据库中没有找到任何股票推荐数据",
                "recommendations": []
            }
        
        latest_date = latest_dates[0]["_id"]
        
        # 处理日期格式
        if isinstance(latest_date, datetime):
            date_str = latest_date.strftime('%Y-%m-%d')
        else:
            date_str = str(latest_date)
        
        # 调用daily-pitch接口获取数据
        return await get_daily_pitch(
            date=date_str,
            collection_name=collection_name,
            limit=limit,
            include_stats=include_stats
        )
        
    except Exception as e:
        logger.error(f"获取最新推荐过程中发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

@router.get(
    "/available-dates",
    summary="获取所有有推荐数据的日期",
    description="查询数据库中所有存在股票推荐（buy_signal=1）的日期"
)
async def get_available_dates(
    collection_name: str = Query(DEFAULT_COLLECTION, description="MongoDB集合名称"),
    limit: int = Query(30, description="返回最近多少天的日期", ge=1, le=365)
):
    """
    获取所有有推荐数据的日期列表
    
    - **collection_name**: MongoDB集合名称
    - **limit**: 返回最近多少天的日期（按日期倒序）
    """
    try:
        db = get_mongo_db()
        collection = db[collection_name]
        
        # 使用聚合查询获取所有不同的日期（只统计有buy_signal=1的日期）
        pipeline = [
            {"$match": {"buy_signal": 1}},
            {"$group": {
                "_id": "$date",
                "count": {"$sum": 1}
            }},
            {"$sort": {"_id": -1}},
            {"$limit": limit}
        ]
        
        cursor = collection.aggregate(pipeline)
        dates = []
        
        async for doc in cursor:
            date_value = doc["_id"]
            # 格式化日期
            if isinstance(date_value, datetime):
                date_str = date_value.strftime('%Y-%m-%d')
            else:
                date_str = str(date_value)
            
            dates.append({
                "date": date_str,
                "recommendation_count": doc["count"]
            })
        
        # 获取最早和最晚日期
        if dates:
            earliest = dates[-1]["date"] if dates else None
            latest = dates[0]["date"] if dates else None
            
            return {
                "total_dates": len(dates),
                "earliest_date": earliest,
                "latest_date": latest,
                "dates": dates
            }
        else:
            return {
                "message": "没有找到任何推荐数据",
                "total_dates": 0,
                "dates": []
            }
        
    except Exception as e:
        logger.error(f"查询日期列表时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

@router.get(
    "/stock-history",
    summary="获取单只股票的历史推荐",
    description="查询指定股票代码的历史推荐记录"
)
async def get_stock_history(
    stock_code: str = Query(..., description="股票代码"),
    collection_name: str = Query(DEFAULT_COLLECTION, description="MongoDB集合名称"),
    start_date: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(30, description="返回记录数限制", ge=1, le=100)
):
    """
    获取单只股票的历史推荐记录
    
    - **stock_code**: 股票代码
    - **collection_name**: MongoDB集合名称
    - **start_date**: 开始日期 (可选)
    - **end_date**: 结束日期 (可选)
    - **limit**: 返回记录数限制
    """
    try:
        db = get_mongo_db()
        collection = db[collection_name]
        
        # 构建查询条件
        query = {
            "stock_code": stock_code,
            "buy_signal": 1
        }
        
        # 添加日期范围
        date_query = {}
        if start_date:
            try:
                datetime.strptime(start_date, '%Y-%m-%d')
                date_query["$gte"] = start_date
            except ValueError:
                raise HTTPException(status_code=400, detail="开始日期格式错误")
        
        if end_date:
            try:
                datetime.strptime(end_date, '%Y-%m-%d')
                date_query["$lte"] = end_date
            except ValueError:
                raise HTTPException(status_code=400, detail="结束日期格式错误")
        
        if date_query:
            query["date"] = date_query
        
        # 查询总数
        total_count = await collection.count_documents(query)
        
        # 查询记录，按日期降序排序
        cursor = collection.find(query).sort("date", DESCENDING).limit(limit)
        
        # 转换结果
        history = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            
            history.append({
                "date": doc.get("date"),
                "stock_name": doc.get("stock_name"),
                "industry": doc.get("industry"),
                "timing_indicator": doc.get("timing_indicator"),
                "trend_score": doc.get("trend_score"),
                "emotion_index": doc.get("emotion_index"),
                "combined_trend_score": doc.get("combined_trend_score"),
                "anchored_trend_score": doc.get("anchored_trend_score")
            })
        
        # 计算统计数据
        stats = {}
        if history:
            timing_values = [h["timing_indicator"] for h in history if h["timing_indicator"] is not None]
            if timing_values:
                stats = {
                    "avg_timing_indicator": round(sum(timing_values) / len(timing_values), 4),
                    "max_timing_indicator": max(timing_values),
                    "min_timing_indicator": min(timing_values),
                    "total_recommendations": total_count
                }
        
        return {
            "stock_code": stock_code,
            "stock_name": history[0]["stock_name"] if history else None,
            "total_records": total_count,
            "returned_records": len(history),
            "date_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "stats": stats,
            "history": history
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询股票历史时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

@router.get(
    "/industry-summary",
    summary="获取行业分布统计",
    description="按行业统计推荐股票的分布情况"
)
async def get_industry_summary(
    date: Optional[str] = Query(None, description="指定日期 (YYYY-MM-DD)，不指定则使用最新日期"),
    collection_name: str = Query(DEFAULT_COLLECTION, description="MongoDB集合名称")
):
    """
    获取行业分布统计
    
    - **date**: 指定日期，不指定则使用最新日期
    - **collection_name**: MongoDB集合名称
    """
    try:
        db = get_mongo_db()
        collection = db[collection_name]
        
        # 确定查询日期
        query_date = date
        if not query_date:
            # 获取最新日期
            latest_pipeline = [
                {"$match": {"buy_signal": 1}},
                {"$group": {"_id": "$date"}},
                {"$sort": {"_id": -1}},
                {"$limit": 1}
            ]
            cursor = collection.aggregate(latest_pipeline)
            latest_dates = await cursor.to_list(length=1)
            
            if not latest_dates:
                return {"message": "没有找到任何数据"}
            
            latest_date = latest_dates[0]["_id"]
            if isinstance(latest_date, datetime):
                query_date = latest_date.strftime('%Y-%m-%d')
            else:
                query_date = str(latest_date)
        
        # 构建查询
        date_query = {
            "$or": [
                {"date": query_date},
                {"date": datetime.strptime(query_date, '%Y-%m-%d') if isinstance(query_date, str) else query_date}
            ]
        }
        
        # 按行业聚合统计
        pipeline = [
            {"$match": {"$and": [date_query, {"buy_signal": 1}]}},
            {"$group": {
                "_id": "$industry",
                "count": {"$sum": 1},
                "stocks": {"$push": {
                    "stock_code": "$stock_code",
                    "stock_name": "$stock_name",
                    "timing_indicator": "$timing_indicator"
                }},
                "avg_timing_indicator": {"$avg": "$timing_indicator"},
                "max_timing_indicator": {"$max": "$timing_indicator"}
            }},
            {"$sort": {"count": -1}}
        ]
        
        cursor = collection.aggregate(pipeline)
        industries = []
        total_stocks = 0
        
        async for doc in cursor:
            # 对每个行业的股票按timing_indicator排序
            stocks = sorted(
                doc["stocks"], 
                key=lambda x: x["timing_indicator"] if x["timing_indicator"] is not None else -float('inf'), 
                reverse=True
            )
            
            industries.append({
                "industry": doc["_id"] if doc["_id"] else "未分类",
                "stock_count": doc["count"],
                "avg_timing_indicator": round(doc["avg_timing_indicator"], 4) if doc["avg_timing_indicator"] else None,
                "max_timing_indicator": doc["max_timing_indicator"],
                "stocks": stocks[:5]  # 只返回前5只
            })
            total_stocks += doc["count"]
        
        return {
            "date": query_date,
            "total_stocks": total_stocks,
            "industry_count": len(industries),
            "industries": industries
        }
        
    except Exception as e:
        logger.error(f"获取行业统计时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")

async def get_date_stats(collection, date):
    """获取指定日期的统计数据"""
    try:
        # 构建查询条件
        query = {
            "$and": [
                {
                    "$or": [
                        {"date": date},
                        {"date": datetime.strptime(date, '%Y-%m-%d')}
                    ]
                },
                {"buy_signal": 1}
            ]
        }
        
        # 聚合统计
        pipeline = [
            {"$match": query},
            {"$group": {
                "_id": None,
                "avg_timing_indicator": {"$avg": "$timing_indicator"},
                "max_timing_indicator": {"$max": "$timing_indicator"},
                "min_timing_indicator": {"$min": "$timing_indicator"},
                "avg_trend_score": {"$avg": "$trend_score"},
                "avg_emotion_index": {"$avg": "$emotion_index"},
                "total_count": {"$sum": 1},
                "industries": {"$addToSet": "$industry"}
            }}
        ]
        
        cursor = collection.aggregate(pipeline)
        stats_list = await cursor.to_list(length=1)
        
        if stats_list:
            stats = stats_list[0]
            return {
                "avg_timing_indicator": round(stats.get("avg_timing_indicator", 0), 4) if stats.get("avg_timing_indicator") else None,
                "max_timing_indicator": round(stats.get("max_timing_indicator", 0), 4) if stats.get("max_timing_indicator") else None,
                "min_timing_indicator": round(stats.get("min_timing_indicator", 0), 4) if stats.get("min_timing_indicator") else None,
                "avg_trend_score": round(stats.get("avg_trend_score", 0), 4) if stats.get("avg_trend_score") else None,
                "avg_emotion_index": round(stats.get("avg_emotion_index", 0), 4) if stats.get("avg_emotion_index") else None,
                "total_recommendations": stats.get("total_count", 0),
                "industry_count": len(stats.get("industries", []))
            }
        else:
            return None
            
    except Exception as e:
        logger.error(f"获取统计数据失败: {str(e)}")
        return None