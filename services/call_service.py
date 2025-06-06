# services/call_service.py
from datetime import datetime
from typing import Optional, Dict, Any, List
from bson import ObjectId
from config.database import get_database
import logging

logger = logging.getLogger(__name__)

class CallService:
    def __init__(self):
        self.collection_name = "calls"
    
    async def get_collection(self):
        """Get the calls collection"""
        db = get_database()
        return db[self.collection_name]
    
    async def get_all_calls(self) -> List[str]:
        """Get all calls from the database"""
        try:
            collection = await self.get_collection()
            # ERROR:services.call_service:Error fetching all calls: 'AsyncIOMotorCursor' object is not iterable
            
            calls = collection.find({})
            print(f"Calls: {calls}")
            return calls
        except Exception as e:
            logger.error(f"Error fetching all calls: {e}")
            return []
    
    async def save_call(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save call data to MongoDB"""
        try:
            collection = await self.get_collection()
            
            call_document = {
                "call_id": call_data.get("call_id"),
                "interviewer_id": call_data.get("interviewer_id"),
                "interview_id": call_data.get("interview_id"),
                "dynamic_data": call_data.get("dynamic_data"),
                "transcripts": "",  # Initially empty
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "status": "registered",
                "call_analysis": call_data.get("call_analysis", {}),
                "end_timestamp": None,  # Initially None
                "start_timestamp": None
            }
            
            result = await collection.insert_one(call_document)
            
            logger.info(f"Call saved to database with ID: {result.inserted_id}")
            
            return {
                "success": True,
                "inserted_id": str(result.inserted_id),
                "call_id": call_data.get("call_id")
            }
            
        except Exception as e:
            logger.error(f"Error saving call to database: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    
    
    async def get_call(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get call by call_id"""
        try:
            collection = await self.get_collection()
            call = await collection.find_one({"call_id": call_id})
            
            if call:
                call["_id"] = str(call["_id"])  # Convert ObjectId to string
            
            return call
            
        except Exception as e:
            logger.error(f"Error fetching call from database: {e}")
            return None
    
    async def update_call_status(self, call_id: str, status: str, additional_data: Optional[Dict] = None) -> Dict[str, Any]:
        """Update call status"""
        try:
            collection = await self.get_collection()
            
            update_doc = {
                "status": status,
                "updated_at": datetime.utcnow()
            }
            
            if additional_data:
                update_doc.update(additional_data)
            
            result = await collection.update_one(
                {"call_id": call_id},
                {"$set": update_doc}
            )
            
            return {
                "success": result.modified_count > 0,
                "modified_count": result.modified_count
            }
            
        except Exception as e:
            logger.error(f"Error updating call status: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_calls_by_interviewer(self, interviewer_id: str) -> List[Dict[str, Any]]:
        """Get all calls for a specific interviewer"""
        try:
            collection = await self.get_collection()
            cursor = collection.find({"interviewer_id": interviewer_id}).sort("created_at", -1)
            calls = await cursor.to_list(length=None)
            
            # Convert ObjectId to string for JSON serialization
            for call in calls:
                call["_id"] = str(call["_id"])
            
            return calls
            
        except Exception as e:
            logger.error(f"Error fetching calls by interviewer: {e}")
            return []

    async def add_transcripts(self, call_id: str, transcripts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Add transcripts to a call"""
        try:
            collection = await self.get_collection()
            
            update_doc = {
                "transcripts": transcripts,
                "updated_at": datetime.utcnow()
            }
            
            result = await collection.update_one(
                {"call_id": call_id},
                {"$set": update_doc}
            )
            
            if result.modified_count > 0:
                logger.info(f"Transcripts added to call {call_id}")
                return {
                    "success": True,
                    "modified_count": result.modified_count,
                    "call_id": call_id
                }
            else:
                logger.warning(f"Call {call_id} not found or transcripts not updated")
                return {
                    "success": False,
                    "error": "Call not found or transcripts not updated"
                }
            
        except Exception as e:
            logger.error(f"Error adding transcripts to call {call_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def add_transcript_and_timestamp(self, call_id: str, transcripts: str, start_timestamp: int, end_timestamp: int) -> Dict[str, Any]:
        """Add transcripts and timestamps to a call"""
        try:
            collection = await self.get_collection()
            
            update_doc = {
                "transcripts": transcripts,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "updated_at": datetime.utcnow()
            }
            
            result = await collection.update_one(
                {"call_id": call_id},
                {"$set": update_doc}
            )
            
            if result.modified_count > 0:
                logger.info(f"Transcripts and timestamps added to call {call_id}")
                return {
                    "success": True,
                    "modified_count": result.modified_count,
                    "call_id": call_id
                }
            else:
                logger.warning(f"Call {call_id} not found or transcripts not updated")
                return {
                    "success": False,
                    "error": "Call not found or transcripts not updated"
                }
            
        except Exception as e:
            logger.error(f"Error adding transcripts and timestamps to call {call_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def append_transcript(self, call_id: str, transcript_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Append a single transcript entry to existing transcripts"""
        try:
            collection = await self.get_collection()
            
            # Add timestamp to transcript entry if not present
            if "timestamp" not in transcript_entry:
                transcript_entry["timestamp"] = datetime.utcnow()
            
            result = await collection.update_one(
                {"call_id": call_id},
                {
                    "$push": {"transcripts": transcript_entry},
                    "$set": {"updated_at": datetime.utcnow()}
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"Transcript entry appended to call {call_id}")
                return {
                    "success": True,
                    "modified_count": result.modified_count,
                    "call_id": call_id
                }
            else:
                logger.warning(f"Call {call_id} not found or transcript not appended")
                return {
                    "success": False,
                    "error": "Call not found or transcript not appended"
                }
            
        except Exception as e:
            logger.error(f"Error appending transcript to call {call_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

# Create a singleton instance
call_service = CallService()