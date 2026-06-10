import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useCaseStore = defineStore('case', () => {
  const currentProject = ref(null)
  const uploadedImages = ref([])
  const generatedTestcases = ref([])
  const selectedAI = ref('deepseek')
  const selectedCaseTypes = ref(['functional', 'ui', 'boundary', 'exception'])
  
  function setCurrentProject(project) {
    currentProject.value = project
  }
  
  function addUploadedImage(image) {
    uploadedImages.value.push(image)
  }
  
  function removeUploadedImage(imageId) {
    const index = uploadedImages.value.findIndex(img => img.id === imageId)
    if (index > -1) {
      uploadedImages.value.splice(index, 1)
    }
  }
  
  function clearUploadedImages() {
    uploadedImages.value = []
  }
  
  function setGeneratedTestcases(testcases) {
    generatedTestcases.value = testcases
  }
  
  function addGeneratedTestcases(testcases) {
    generatedTestcases.value.push(...testcases)
  }
  
  function clearGeneratedTestcases() {
    generatedTestcases.value = []
  }
  
  function setSelectedAI(ai) {
    selectedAI.value = ai
  }
  
  function setSelectedCaseTypes(types) {
    selectedCaseTypes.value = types
  }
  
  return {
    currentProject,
    uploadedImages,
    generatedTestcases,
    selectedAI,
    selectedCaseTypes,
    setCurrentProject,
    addUploadedImage,
    removeUploadedImage,
    clearUploadedImages,
    setGeneratedTestcases,
    addGeneratedTestcases,
    clearGeneratedTestcases,
    setSelectedAI,
    setSelectedCaseTypes
  }
})