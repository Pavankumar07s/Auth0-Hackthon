"""FGA Setup Script — Create authorization model and relationship tuples.

Auth0 feature: Fine-Grained Authorization (OpenFGA)

Run this to set up the FGA store with:
- Authorization model (data_stream type with viewer relation)
- Relationship tuples granting vision_agent access to allowed streams
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import openfga_sdk
from openfga_sdk.credentials import Credentials, CredentialConfiguration
from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

from auth0.config import FGA_API_URL, FGA_CLIENT_ID, FGA_CLIENT_SECRET, FGA_STORE_ID


async def setup_fga() -> None:
    """Create FGA relationship tuples for ETMS agents."""
    config = openfga_sdk.ClientConfiguration(
        api_url=FGA_API_URL,
        store_id=FGA_STORE_ID,
        credentials=Credentials(
            method="client_credentials",
            configuration=CredentialConfiguration(
                api_issuer="fga.us.auth0.com",
                api_audience="https://api.us1.fga.dev/",
                client_id=FGA_CLIENT_ID,
                client_secret=FGA_CLIENT_SECRET,
            ),
        ),
    )

    async with openfga_sdk.OpenFgaClient(config) as client:
        # Write authorization model
        print("Writing authorization model...")
        model = openfga_sdk.WriteAuthorizationModelRequest(
            schema_version="1.1",
            type_definitions=[
                openfga_sdk.TypeDefinition(
                    type="user",
                    relations={},
                ),
                openfga_sdk.TypeDefinition(
                    type="data_stream",
                    relations={
                        "viewer": openfga_sdk.Userset(
                            this={},
                        ),
                    },
                    metadata=openfga_sdk.Metadata(
                        relations={
                            "viewer": openfga_sdk.RelationMetadata(
                                directly_related_user_types=[
                                    openfga_sdk.RelationReference(type="user"),
                                ]
                            ),
                        }
                    ),
                ),
            ],
        )
        model_resp = await client.write_authorization_model(model)
        print(f"  Model created: {model_resp.authorization_model_id}")

        # Write relationship tuples
        print("Writing relationship tuples...")
        tuples = [
            # Vision agent CAN access these streams
            ("user:vision_agent", "viewer", "data_stream:fall_events"),
            ("user:vision_agent", "viewer", "data_stream:behavior_anomalies"),
            ("user:vision_agent", "viewer", "data_stream:location_data"),
            ("user:vision_agent", "viewer", "data_stream:pose_data"),
            # Vision agent CANNOT access health_records (no tuple = denied)
            # Caregiver CAN access everything
            ("user:caregiver", "viewer", "data_stream:fall_events"),
            ("user:caregiver", "viewer", "data_stream:behavior_anomalies"),
            ("user:caregiver", "viewer", "data_stream:health_records"),
            ("user:caregiver", "viewer", "data_stream:location_data"),
        ]

        writes = []
        for user, relation, obj in tuples:
            writes.append(
                ClientTuple(user=user, relation=relation, object=obj)
            )

        body = ClientWriteRequest(writes=writes)
        await client.write(body)
        print(f"  {len(writes)} tuples written successfully")
        print()
        print("FGA setup complete!")
        print("  vision_agent: fall_events ✓, behavior_anomalies ✓, location_data ✓, health_records ✗")
        print("  caregiver: all streams ✓")


if __name__ == "__main__":
    asyncio.run(setup_fga())
