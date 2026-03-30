"""
Auth0-powered action handlers for OpenClaw emergency dispatch.

Wraps existing ActionDispatcher with Auth0 security features:
- Token Vault for Slack/Calendar API calls
- CIBA for caregiver approval before critical dispatch
- Step-Up authentication for emergency services
- FGA authorization checks before any action

Auth0 Features Used: Token Vault, CIBA, FGA, Step-Up Auth
"""

import logging
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timezone

import sys
import os

# Add parent directory to path for auth0 module imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth0.token_vault import (
    post_slack_alert,
    create_google_calendar_event,
    exchange_token_for_connection,
)
from auth0.ciba import critical_dispatch_with_approval, request_backchannel_authorization
from auth0.fga import is_authorized
from auth0.step_up import verify_step_up_satisfied
from auth0.config import AUTH0_DOMAIN

logger = logging.getLogger("openclaw.auth0_actions")


class Auth0ActionHandler:
    """
    Auth0-secured action handler for ETMS incidents.
    
    Wraps the existing ActionDispatcher with Auth0 security layers:
    1. FGA checks before any action
    2. Token Vault for third-party API calls
    3. CIBA approval for critical dispatches
    4. Step-Up auth for emergency services
    """
    
    def __init__(self, user_id: str = "caregiver"):
        """
        Initialize the Auth0 action handler.
        
        Args:
            user_id: The Auth0 user identifier for authorization checks
        """
        self.user_id = user_id
        self.dispatch_log: list[Dict[str, Any]] = []
    
    async def handle_incident(
        self,
        incident_id: str,
        severity: str,
        description: str,
        context: Optional[Dict[str, Any]] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle an ETMS incident with full Auth0 security chain.
        
        Flow:
        1. FGA check: Is the acting agent authorized?
        2. Low/Medium severity: Token Vault → Slack alert + Calendar event
        3. High severity: CIBA push → Caregiver approval → Dispatch
        4. Critical severity: Step-Up MFA → CIBA → Emergency dispatch
        
        Args:
            incident_id: Unique incident identifier
            severity: Incident severity (low, medium, high, critical)
            description: Human-readable incident description
            context: Additional context (location, sensor data, etc.)
            access_token: Auth0 access token for Token Vault operations
            
        Returns:
            Dict with dispatch results and Auth0 actions taken
        """
        logger.info(f"Handling incident {incident_id} (severity: {severity})")
        
        result = {
            "incident_id": incident_id,
            "severity": severity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "auth0_actions": [],
            "dispatch_status": "pending",
        }
        
        # Step 1: FGA Authorization Check
        try:
            authorized = await asyncio.to_thread(
                is_authorized, "vision_agent", "viewer", "data_stream", "vision_feed"
            )
            result["auth0_actions"].append({
                "feature": "FGA",
                "action": "authorization_check",
                "result": "authorized" if authorized else "denied",
            })
            
            if not authorized:
                result["dispatch_status"] = "denied_by_fga"
                logger.warning(f"FGA denied access for incident {incident_id}")
                return result
        except Exception as e:
            logger.error(f"FGA check failed: {e}")
            result["auth0_actions"].append({
                "feature": "FGA",
                "action": "authorization_check",
                "result": f"error: {str(e)}",
            })
        
        # Step 2: Route by severity
        if severity in ("low", "medium"):
            return await self._handle_low_medium(result, description, access_token)
        elif severity == "high":
            return await self._handle_high(result, description, access_token)
        elif severity == "critical":
            return await self._handle_critical(result, description, access_token)
        else:
            result["dispatch_status"] = "unknown_severity"
            return result
    
    async def _handle_low_medium(
        self,
        result: Dict[str, Any],
        description: str,
        access_token: Optional[str],
    ) -> Dict[str, Any]:
        """
        Handle low/medium severity: Slack notification + Calendar event via Token Vault.
        
        Auth0 Features: Token Vault (Slack, Google Calendar)
        """
        logger.info("Low/Medium severity — sending notifications via Token Vault")
        
        # Slack alert via Token Vault
        try:
            slack_result = post_slack_alert(
                message=f"🔔 ETMS Alert: {description}",
                channel="#etms-alerts",
            )
            result["auth0_actions"].append({
                "feature": "Token Vault",
                "action": "slack_alert",
                "result": "sent" if slack_result else "failed",
            })
        except Exception as e:
            logger.error(f"Slack alert failed: {e}")
            result["auth0_actions"].append({
                "feature": "Token Vault",
                "action": "slack_alert",
                "result": f"error: {str(e)}",
            })
        
        # Calendar event via Token Vault
        try:
            cal_result = create_google_calendar_event(
                title=f"ETMS Incident: {result['incident_id']}",
                description=description,
                duration_minutes=30,
            )
            result["auth0_actions"].append({
                "feature": "Token Vault",
                "action": "calendar_event",
                "result": "created" if cal_result else "failed",
            })
        except Exception as e:
            logger.error(f"Calendar event failed: {e}")
            result["auth0_actions"].append({
                "feature": "Token Vault",
                "action": "calendar_event",
                "result": f"error: {str(e)}",
            })
        
        result["dispatch_status"] = "notified"
        self.dispatch_log.append(result)
        return result
    
    async def _handle_high(
        self,
        result: Dict[str, Any],
        description: str,
        access_token: Optional[str],
    ) -> Dict[str, Any]:
        """
        Handle high severity: CIBA push approval → then dispatch.
        
        Auth0 Features: CIBA (backchannel authorization), Token Vault
        """
        logger.info("High severity — requesting CIBA approval")
        
        binding_message = f"ETMS HIGH ALERT: {description}. Approve emergency response?"
        
        try:
            ciba_result = critical_dispatch_with_approval(
                binding_message=binding_message,
                dispatch_callback=lambda: self._execute_dispatch(result, description),
            )
            result["auth0_actions"].append({
                "feature": "CIBA",
                "action": "backchannel_authorization",
                "result": "approved" if ciba_result else "denied_or_timeout",
            })
            
            if ciba_result:
                result["dispatch_status"] = "dispatched_with_approval"
            else:
                result["dispatch_status"] = "pending_approval"
        except Exception as e:
            logger.error(f"CIBA flow failed: {e}")
            result["auth0_actions"].append({
                "feature": "CIBA",
                "action": "backchannel_authorization",
                "result": f"error: {str(e)}",
            })
            result["dispatch_status"] = "ciba_error"
        
        self.dispatch_log.append(result)
        return result
    
    async def _handle_critical(
        self,
        result: Dict[str, Any],
        description: str,
        access_token: Optional[str],
    ) -> Dict[str, Any]:
        """
        Handle critical severity: Step-Up MFA → CIBA → Emergency dispatch.
        
        Auth0 Features: Step-Up Auth, CIBA, Token Vault
        """
        logger.info("CRITICAL severity — requiring Step-Up + CIBA")
        
        # Step-Up MFA check
        if access_token:
            try:
                step_up_ok = verify_step_up_satisfied(access_token)
                result["auth0_actions"].append({
                    "feature": "Step-Up Auth",
                    "action": "mfa_verification",
                    "result": "satisfied" if step_up_ok else "required",
                })
                
                if not step_up_ok:
                    result["dispatch_status"] = "awaiting_step_up"
                    logger.warning("Step-up MFA required for critical dispatch")
                    self.dispatch_log.append(result)
                    return result
            except Exception as e:
                logger.error(f"Step-up check failed: {e}")
                result["auth0_actions"].append({
                    "feature": "Step-Up Auth",
                    "action": "mfa_verification",
                    "result": f"error: {str(e)}",
                })
        
        # CIBA push for critical dispatch
        binding_message = (
            f"🚨 CRITICAL ETMS ALERT: {description}. "
            f"Approve emergency services dispatch?"
        )
        
        try:
            ciba_result = critical_dispatch_with_approval(
                binding_message=binding_message,
                dispatch_callback=lambda: self._execute_emergency_dispatch(result, description),
            )
            result["auth0_actions"].append({
                "feature": "CIBA",
                "action": "critical_dispatch_approval",
                "result": "approved" if ciba_result else "denied_or_timeout",
            })
            
            if ciba_result:
                result["dispatch_status"] = "emergency_dispatched"
            else:
                result["dispatch_status"] = "pending_critical_approval"
        except Exception as e:
            logger.error(f"Critical CIBA flow failed: {e}")
            result["auth0_actions"].append({
                "feature": "CIBA",
                "action": "critical_dispatch_approval",
                "result": f"error: {str(e)}",
            })
            result["dispatch_status"] = "critical_ciba_error"
        
        self.dispatch_log.append(result)
        return result
    
    def _execute_dispatch(self, result: Dict[str, Any], description: str) -> bool:
        """Execute standard alert dispatch after CIBA approval."""
        logger.info(f"Executing dispatch for incident {result['incident_id']}")
        try:
            post_slack_alert(
                message=f"⚠️ APPROVED DISPATCH: {description}",
                channel="#etms-alerts",
            )
            return True
        except Exception as e:
            logger.error(f"Dispatch execution failed: {e}")
            return False
    
    def _execute_emergency_dispatch(self, result: Dict[str, Any], description: str) -> bool:
        """Execute emergency dispatch after Step-Up + CIBA approval."""
        logger.info(f"🚨 Executing EMERGENCY dispatch for incident {result['incident_id']}")
        try:
            post_slack_alert(
                message=f"🚨 EMERGENCY DISPATCH ACTIVATED: {description}",
                channel="#etms-emergency",
            )
            create_google_calendar_event(
                title=f"🚨 EMERGENCY: {result['incident_id']}",
                description=f"Emergency services dispatched. {description}",
                duration_minutes=60,
            )
            return True
        except Exception as e:
            logger.error(f"Emergency dispatch failed: {e}")
            return False
    
    def get_dispatch_history(self) -> list[Dict[str, Any]]:
        """Return the full dispatch audit log."""
        return self.dispatch_log
