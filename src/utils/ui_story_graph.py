import io
from typing import List

from langgraph.graph import StateGraph, END
import base64
from fastapi import UploadFile
from PIL import Image

from src.schemas.response import ResponseModel
from src.services.azure_services import AzureChatService
from src.schemas.ui_story_graph_request import UIStoryState
from src.schemas.user_data import UserData
from src.services.setup.variables_setup import LLMOPS_API_KEY
from src.prompts.user_story_generation import GENERATE_ANALYSIS_OF_THE_UI_PROMPT, GENERATE_USER_STORIES_FROM_UI_PROMPT
from src.utils.epics import get_epic_by_id
from src.utils.user_stories import create_multiple_user_stories
from src.utils.templates import generate_template_formating, get_selected_template_by_project

async def resize_and_convert_to_base64(file_bytes: bytes, max_size: int = 1080) -> str:
    """
    Recibe los bytes de una imagen, la redimensiona si excede el max_size
    y la convierte a un string Base64.
    """
    # Abrir la imagen en memoria usando Pillow directamente con los bytes
    image = Image.open(io.BytesIO(file_bytes))
    
    # Verificar si necesita redimensionamiento
    if image.width > max_size or image.height > max_size:
        print(f"Redimensionando imagen de {image.width}x{image.height} a un máximo de {max_size}px")
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    
    # Guardar la imagen procesada en un nuevo buffer de memoria
    buffered = io.BytesIO()
    img_format = image.format if image.format else "PNG"
    image.save(buffered, format=img_format)
    
    # Convertir a Base64
    base64_encoded = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    return base64_encoded

async def node_analyze_ui(state: UIStoryState, azure_services: AzureChatService) -> dict:
    """Nodo 1: Usa el modelo de visión para extraer qué hay en la imagen."""
    print("-> Ejecutando Nodo: Analizando UI...")

    prompt = GENERATE_ANALYSIS_OF_THE_UI_PROMPT.format(
        project_description=state.get('project_description', '')
    )

    # Uso del servicio con soporte de imágenes
    response = await azure_services.completion_without_knowledge_base(
        prompt=prompt,
        images=state['image_base64'],
        expected_keys=["ui_analysis"]
    )

    if response and isinstance(response, dict) and "ui_analysis" in response:
        # LangGraph tomará el return y lo inyectará en 'ui_analysis' de 'caja'
        return { "ui_analysis": response }
    
    return {"error": "El modelo de visión no pudo generar el JSON de análisis."}

async def node_generate_user_stories(state: UIStoryState, azure_services: AzureChatService) -> dict:
    """Nodo 2: Crea el JSON de las historias usando el análisis previo."""
    print("-> Ejecutando Nodo: Generando User Stories...")
    
    # Si hubo un error en el nodo anterior o no hay análisis, cortamos
    if state.get('error') and not state.get('ui_analysis'):
        return {}

    prompt = GENERATE_USER_STORIES_FROM_UI_PROMPT.format(
        project_description=state.get('project_description', ''),
        ui_analysis=state.get('ui_analysis', ''),
        epic_name=state.get('epic_name', ''),
        #template_fields_json=state.get('template_fields_json', ''),
        fields_description=state.get('fields_description', ''),
    )

    # Si venimos de un reintento (falló la validación), le avisamos al LLM
    if state.get('error'):
        prompt += f"\n\nNOTE: Your previous attempt failed due to this error: '{state['error']}. Please correct the format and generate the JSON correctly."

    base_keys = ["epic", "user_story_id", "user_story", "description", "order", "story_points", "dependencies"]
    expected_keys = base_keys + state.get('template_field_keys', [])

    response = await azure_services.completion_without_knowledge_base(
        prompt=prompt,
        key="user_stories",
        expected_keys=expected_keys
    )

    if not response:
        return {"error": "El formato JSON generado no es válido o faltan campos."}

    return { "user_stories": response }

async def node_validate_format(state: UIStoryState) -> dict:
    """Nodo 3: Revisa que la estructura de los datos sea perfecta antes de terminar."""
    print("-> Ejecutando Nodo 3: Validando estructura...")

    current_retries = state.get("retry_count", 0) + 1

    if state.get("error"):
        return {"retry_count": current_retries}

    stories = state.get("user_stories")

    if not stories or not isinstance(stories, list) or len(stories) == 0:
        return {"error": "La lista de historias generada está vacía o no es una lista.", "retry_count": current_retries}

    # Validar que cada historia tenga las llaves mínimas necesarias
    required_keys = {"user_story_id", "user_story", "description", "story_points"}
    for story in stories:
        missing_keys = required_keys - set(story.keys())
        if missing_keys:
            return {
                "error": f"A algunas historias les faltan estas llaves obligatorias: {missing_keys}", 
                "retry_count": current_retries
            }

    print("-> Aplicando auto-correción de dependencias...")

    # Forzamos que la primera historia sea el nodo raíz
    stories[0]["dependencies"] = []

    # Obtenemos todos los IDs válidos que el LLM realmente generó en esta pasada
    valid_ids = [story["user_story_id"] for story in stories]

    # Limpiamos las dependencias fantasma en el resto de las historias
    for story in stories:
        # Filtramos para quedarnos solo con las dependencias que existen en la lista de valid_ids
        story["dependencies"] = [dep for dep in story.get("dependencies", []) if dep in valid_ids]

    # Forzamos matemáticamente que el orden empiece en 1 y sea secuencial
    for index, story in enumerate(stories):
        story["order"] = index + 1

    return {"error": None, "retry_count": current_retries, "user_stories": stories}

def check_validation(state: UIStoryState) -> str:
    """Decide a qué nodo ir después de la validación."""
    error = state.get("error")
    retry_count = state.get("retry_count", 0)

    if error:
        if retry_count < 3:
            print(f"Error detectado ('{error}'). Reintentando (Intento {retry_count}/3)...")
            return "retry" # Regresa al generador
        else:
            print("Se alcanzó el limite máximo de reintentos.")
            return "end"

    return "end"

def build_ui_story_graph(azure_services: AzureChatService):
    # Iniciamos el grafo indicando qué "molde" de estado usará
    workflow = StateGraph(UIStoryState)

    # Agregamos los nodos
    # Usamos la función lambda para poder usar el azure_services
    # workflow.add_node("vision_analysis", lambda state: node_analyze_ui(state, azure_services))
    # workflow.add_node("story_generation", lambda state: node_generate_user_stories(state, azure_services))

    # wrappers asincronos
    async def wrap_analyze_ui(state: UIStoryState):
        return await node_analyze_ui(state, azure_services)
        
    async def wrap_generate_stories(state: UIStoryState):
        return await node_generate_user_stories(state, azure_services)

    async def wrap_validate(state: UIStoryState):
        return await node_validate_format(state)

    # 2. Agregamos los nodos usando los wrappers
    workflow.add_node("vision_analysis", wrap_analyze_ui)
    workflow.add_node("user_story_generation", wrap_generate_stories)
    workflow.add_node("validation", wrap_validate)

    # Definimos el camino (edges)
    workflow.set_entry_point("vision_analysis")
    workflow.add_edge("vision_analysis", "user_story_generation")
    workflow.add_edge("user_story_generation", "validation")

    workflow.add_conditional_edges(
        "validation",
        check_validation,
        {
            "retry": "user_story_generation", #Si falla, vuelve al nodo 2
            "end": END # Si pasa, termina
        }
    )

    return workflow.compile()

async def generate_user_stories_from_ui(epic_id: str, project_description: str, images: List[UploadFile], user_data: UserData) -> UIStoryState:
    try:
        epic_response = get_epic_by_id(epic_id)
        if not epic_response.success:
            return ResponseModel(success=False, message=f"Epic not found: {epic_response.message}", data=None)

        epic_data = epic_response.data
        epic_name = epic_data.get('title', 'UI Generated Epic')

        template_data = {}
        template_field_keys = []
        template_fields_json = ""
        fields_description = ""

        if epic_data.get("project_id"):
            template_response = get_selected_template_by_project(epic_data["project_id"], user_data.get_user_id())
            if template_response.success:
                template_data = template_response.data
                try:
                    # Generamos los strings igual que en la creación de texto
                    template_field_keys, template_fields_json, fields_description = generate_template_formating(template_data)
                except Exception as e:
                    print(f"Error procesando template: {e}")

        # Convertir la lista de UploadFile (imágenes) a una lista de strings base64
        base64_images = []
        for img in images:
            # Regresar el cursor al inicio ANTES de leer
            await img.seek(0)

            file_bytes = await img.read()
            if len(file_bytes) == 0:
                return ResponseModel(success=False, message=f"La imagen {img.filename} llegó vacía o corrupta al servidor.")

            base64_encoded = await resize_and_convert_to_base64(file_bytes, max_size=1080)
            base64_images.append(base64_encoded)

        # Correr el grafo de LangGraph
        azure_services = AzureChatService(LLMOPS_API_KEY, user_data, None)
        graph = build_ui_story_graph(azure_services)

        # El estado inicial
        initial_state = UIStoryState(
            project_description=project_description,
            image_base64=base64_images,
            epic_name=epic_name,
            template_field_keys=template_field_keys,
            template_fields_json=template_fields_json,
            fields_description=fields_description,
            ui_analysis=None,
            user_stories=None,
            error=None,
            retry_count=0
        )

        # Ejecutamos el flujo (ainvoke es async)
        print("Iniciando flujo de LangGraph...")
        final_state = await graph.ainvoke(initial_state)

        if final_state.get('error'):
            return ResponseModel(success=False, message=f"Flujo terminado con error tras validación: {final_state['error']}")

        generated_stories = final_state['user_stories']

        epic_name = epic_data.get('title', 'UI Generated Epic')
        for story in generated_stories:
            story['epic'] = epic_name

        print(f"Guardando {len(generated_stories)} historias en la BD...")

        # return ResponseModel(
        #     success=True,
        #     message="User Stories generadas con éxito",
        #     data={
        #         "user_stories": generated_stories,
        #         "generated_stories": len(generated_stories)
        #     }
        # )

        save_result = create_multiple_user_stories(
            epic_id=epic_id,
            user_id=user_data.get_user_id(),
            user_stories_list=generated_stories,
            template_data=template_data
        )

        if save_result.success:
            return ResponseModel(
                success=True,
                message="User Stories generadas con éxito",
                data={
                    "user_stories": save_result.data,
                    "generated_stories": len(generated_stories)
                }
            )
        else:
            return ResponseModel(success=False, message=f"Error al guardar historias: {save_result.message}", data=None)
    except Exception as e:
        return ResponseModel(success=False, message=str(e))